
from django.core import paginator

import mock

from olympia import amo
from olympia.addons.models import Addon
from olympia.amo import search
from olympia.amo.tests import TestCase, ESTestCaseWithAddons
from olympia.tags.models import Tag


class TestESIndexing(ESTestCaseWithAddons):

    # This needs to be in its own class for data isolation.
    def test_indexed_count(self):
        # Did all the right addons get indexed?
        count = Addon.search().filter(type=1, is_disabled=False).count()
        # Created in the setUpClass.
        assert count == 4 == (
            Addon.objects.filter(disabled_by_user=False,
                                 status__in=amo.VALID_ADDON_STATUSES).count())

    def test_get_es_not_mocked(self):
        es = search.get_es()
        assert not issubclass(es.__class__, mock.Mock)


class TestNoESIndexing(TestCase):

    def test_no_es(self):
        assert not getattr(self, 'es', False), (
            'TestCase should not have "es" attribute')

    def test_not_indexed(self):
        addon = Addon.objects.create(type=amo.ADDON_EXTENSION,
                                     status=amo.STATUS_PUBLIC)
        assert issubclass(
            Addon.search().filter(id__in=addon.id).count().__class__,
            mock.Mock)

    def test_get_es_mocked(self):
        es = search.get_es()
        assert issubclass(es.__class__, mock.Mock)


class TestESWithoutMakingQueries(TestCase):
    # These tests test methods that don't directly call ES, so they work using
    # the faster TestCase class where ES is mocked.

    def test_clone(self):
        # Doing a filter creates a new ES object.
        qs = Addon.search()
        qs2 = qs.filter(type=1)
        assert 'bool' not in qs._build_query()['query']
        assert 'filter' in qs2._build_query()['query']['bool']

    def test_filter(self):
        qs = Addon.search().filter(type=1)
        assert qs._build_query()['query']['bool']['filter'] == (
            [{'term': {'type': 1}}])

    def test_in_filter(self):
        qs = Addon.search().filter(type__in=[1, 2])
        assert qs._build_query()['query']['bool']['filter'] == (
            [{'terms': {'type': [1, 2]}}])

    def test_and(self):
        qs = Addon.search().filter(type=1, category__in=[1, 2])
        filters = qs._build_query()['query']['bool']['filter']['bool']['must']
        # Filters:
        # [{'term': {'type': 1}}, {'terms': {'category': [1, 2]}}]
        assert len(filters) == 2
        assert {'term': {'type': 1}} in filters
        assert {'terms': {'category': [1, 2]}} in filters

    def test_query(self):
        qs = Addon.search().query(type=1)
        assert qs._build_query()['query']['function_score']['query'] == (
            {'term': {'type': 1}})

    def test_query_match(self):
        qs = Addon.search().query(name__match='woo woo')
        assert qs._build_query()['query']['function_score']['query'] == (
            {'match': {'name': 'woo woo'}})

    def test_query_multiple_and_range(self):
        qs = Addon.search().query(type=1, status__gte=1)
        query = qs._build_query()['query']['function_score']['query']
        # Query:
        # {'bool': {'must': [{'term': {'type': 1}},
        #                    {'range': {'status': {'gte': 1}}}, ]}}
        assert query.keys() == ['bool']
        assert query['bool'].keys() == ['must']
        assert {'term': {'type': 1}} in query['bool']['must']
        assert {'range': {'status': {'gte': 1}}} in query['bool']['must']

    def test_query_or(self):
        qs = Addon.search().query(or_={'type': 1, 'status__gte': 2})
        query = qs._build_query()['query']['function_score']['query']
        # Query:
        # {'bool': {'should': [{'term': {'type': 1}},
        #                      {'range': {'status': {'gte': 2}}}, ]}}
        assert query.keys() == ['bool']
        assert query['bool'].keys() == ['should']
        assert {'term': {'type': 1}} in query['bool']['should']
        assert {'range': {'status': {'gte': 2}}} in query['bool']['should']

    def test_query_or_and(self):
        qs = Addon.search().query(
            or_={'type': 1, 'status__gte': 2},
            category=2)
        query = qs._build_query()['query']['function_score']['query']
        # Query:
        # {'bool': {'must': [{'term': {'category': 2}},
        #                    {'bool': {'should': [
        #                        {'term': {'type': 1}},
        #                        {'range': {'status': {'gte': 2}}}, ]}}]}})
        assert query.keys() == ['bool']
        assert query['bool'].keys() == ['must']
        assert {'term': {'category': 2}} in query['bool']['must']
        sub_clause = sorted(query['bool']['must'])[0]
        assert sub_clause.keys() == ['bool']
        assert sub_clause['bool'].keys() == ['should']
        assert (
            {'range': {'status': {'gte': 2}}} in sub_clause['bool']['should'])
        assert {'term': {'type': 1}} in sub_clause['bool']['should']

    def test_query_fuzzy(self):
        fuzz = {'boost': 2, 'value': 'woo'}
        qs = Addon.search().query(or_={'type': 1, 'status__fuzzy': fuzz})
        query = qs._build_query()['query']['function_score']['query']
        # Query:
        # {'bool': {'should': [{'fuzzy': {'status': fuzz}},
        #                      {'term': {'type': 1}}, ]}})
        assert query.keys() == ['bool']
        assert query['bool'].keys() == ['should']
        assert {'term': {'type': 1}} in query['bool']['should']
        assert {'fuzzy': {'status': fuzz}} in query['bool']['should']

    def test_order_by_desc(self):
        qs = Addon.search().order_by('-rating')
        assert qs._build_query()['sort'] == [{'rating': 'desc'}]

    def test_order_by_asc(self):
        qs = Addon.search().order_by('rating')
        assert qs._build_query()['sort'] == ['rating']

    def test_order_by_multiple(self):
        qs = Addon.search().order_by('-rating', 'id')
        assert qs._build_query()['sort'] == [{'rating': 'desc'}, 'id']

    def test_slice(self):
        qs = Addon.search()[5:12]
        assert qs._build_query()['from'] == 5
        assert qs._build_query()['size'] == 7

    def test_filter_or(self):
        qs = Addon.search().filter(type=1).filter(or_={'status': 1, 'app': 2})
        filters = qs._build_query()['query']['bool']['filter']['bool']
        # Filters:
        # {'must': [
        #     {'term': {'type': 1}},
        #     {'should': [{'term': {'status': 1}}, {'term': {'app': 2}}]},
        # ]}
        assert filters.keys() == ['must']
        assert {'term': {'type': 1}} in filters['must']
        should_clause = sorted(filters['must'])[0]
        assert should_clause.keys() == ['should']
        assert {'term': {'status': 1}} in should_clause['should']
        assert {'term': {'app': 2}} in should_clause['should']

        qs = Addon.search().filter(type=1, or_={'status': 1, 'app': 2})
        filters = qs._build_query()['query']['bool']['filter']['bool']
        # Filters:
        # {'must': [
        #     {'term': {'type': 1}},
        #     {'should': [{'term': {'status': 1}}, {'term': {'app': 2}}]},
        # ]}
        assert filters.keys() == ['must']
        assert {'term': {'type': 1}} in filters['must']
        should_clause = sorted(filters['must'])[0]
        assert should_clause.keys() == ['should']
        assert {'term': {'status': 1}} in should_clause['should']
        assert {'term': {'app': 2}} in should_clause['should']

    def test_slice_stop(self):
        qs = Addon.search()[:6]
        assert qs._build_query()['size'] == 6

    def test_slice_stop_zero(self):
        qs = Addon.search()[:0]
        assert qs._build_query()['size'] == 0

    def test_gte(self):
        qs = Addon.search().filter(type__in=[1, 2], status__gte=4)
        filters = qs._build_query()['query']['bool']['filter']['bool']
        # Filters:
        # {'must': [
        #     {'terms': {'type': [1, 2]}},
        #     {'range': {'status': {'gte': 4}}},
        # ]}
        assert filters.keys() == ['must']
        assert {'terms': {'type': [1, 2]}} in filters['must']
        assert {'range': {'status': {'gte': 4}}} in filters['must']

    def test_lte(self):
        qs = Addon.search().filter(type__in=[1, 2], status__lte=4)
        filters = qs._build_query()['query']['bool']['filter']['bool']
        # Filters:
        # {'must': [
        #     {'terms': {'type': [1, 2]}},
        #     {'range': {'status': {'lte': 4}}},
        # ]}
        assert filters.keys() == ['must']
        assert {'terms': {'type': [1, 2]}} in filters['must']
        assert {'range': {'status': {'lte': 4}}} in filters['must']

    def test_gt(self):
        qs = Addon.search().filter(type__in=[1, 2], status__gt=4)
        filters = qs._build_query()['query']['bool']['filter']['bool']
        # Filters:
        # {'must': [
        #     {'terms': {'type': [1, 2]}},
        #     {'range': {'status': {'gt': 4}}},
        # ]}
        assert filters.keys() == ['must']
        assert {'terms': {'type': [1, 2]}} in filters['must']
        assert {'range': {'status': {'gt': 4}}} in filters['must']

    def test_lt(self):
        qs = Addon.search().filter(type__in=[1, 2], status__lt=4)
        filters = qs._build_query()['query']['bool']['filter']['bool']
        # Filters:
        # {'must': [
        #     {'range': {'status': {'lt': 4}}},
        #     {'terms': {'type': [1, 2]}},
        # ]}
        assert filters.keys() == ['must']
        assert {'range': {'status': {'lt': 4}}} in filters['must']
        assert {'terms': {'type': [1, 2]}} in filters['must']

    def test_lt2(self):
        qs = Addon.search().filter(status__lt=4)
        assert qs._build_query()['query']['bool']['filter'] == (
            [{'range': {'status': {'lt': 4}}}])

    def test_range(self):
        qs = Addon.search().filter(date__range=('a', 'b'))
        assert qs._build_query()['query']['bool']['filter'] == (
            [{'range': {'date': {'gte': 'a', 'lte': 'b'}}}])

    def test_prefix(self):
        qs = Addon.search().query(name__startswith='woo')
        assert qs._build_query()['query']['function_score']['query'] == (
            {'prefix': {'name': 'woo'}})

    def test_values(self):
        qs = Addon.search().values('name')
        assert qs._build_query()['_source'] == ['id', 'name']

    def test_values_dict(self):
        qs = Addon.search().values_dict('name')
        assert qs._build_query()['_source'] == ['id', 'name']

    def test_empty_values_dict(self):
        qs = Addon.search().values_dict()
        assert qs._build_query()['_source'] == ['id']

    def test_extra_values(self):
        qs = Addon.search().extra(values=['name'])
        assert qs._build_query()['_source'] == ['id', 'name']

        qs = Addon.search().values('status').extra(values=['name'])
        assert qs._build_query()['_source'] == ['id', 'status', 'name']

    def test_extra_values_dict(self):
        qs = Addon.search().extra(values_dict=['name'])
        assert qs._build_query()['_source'] == ['id', 'name']

        qs = Addon.search().values_dict('status').extra(values_dict=['name'])
        assert qs._build_query()['_source'] == ['id', 'status', 'name']

    def test_extra_order_by(self):
        qs = Addon.search().extra(order_by=['-rating'])
        assert qs._build_query()['sort'] == [{'rating': 'desc'}]

        qs = Addon.search().order_by('-id').extra(order_by=['-rating'])
        assert qs._build_query()['sort'] == [
            {'id': 'desc'}, {'rating': 'desc'}]

    def test_extra_query(self):
        qs = Addon.search().extra(query={'type': 1})
        assert qs._build_query()['query']['function_score']['query'] == (
            {'term': {'type': 1}})

        qs = Addon.search().filter(status=1).extra(query={'type': 1})
        filtered = qs._build_query()['query']['bool']
        assert filtered['must']['function_score']['query'] == (
            {'term': {'type': 1}})
        assert filtered['filter'] == [{'term': {'status': 1}}]

    def test_extra_filter(self):
        qs = Addon.search().extra(filter={'category__in': [1, 2]})
        assert qs._build_query()['query']['bool']['filter'] == (
            [{'terms': {'category': [1, 2]}}])

        qs = (Addon.search().filter(type=1)
              .extra(filter={'category__in': [1, 2]}))
        filters = qs._build_query()['query']['bool']['filter']['bool']['must']
        # Filters:
        # [{'term': {'type': 1}}, {'terms': {'category': [1, 2]}}]
        assert len(filters) == 2
        assert {'term': {'type': 1}} in filters
        assert {'terms': {'category': [1, 2]}} in filters

    def test_extra_filter_or(self):
        qs = Addon.search().extra(filter={'or_': {'status': 1, 'app': 2}})
        filters = qs._build_query()['query']['bool']['filter'][0]
        # Filters:
        # [{'should': [{'term': {'status': 1}}, {'term': {'app': 2}}]}]
        assert len(filters['should']) == 2
        assert {'term': {'status': 1}} in filters['should']
        assert {'term': {'app': 2}} in filters['should']

        qs = (Addon.search().filter(type=1)
              .extra(filter={'or_': {'status': 1, 'app': 2}}))

        filters = qs._build_query()['query']['bool']['filter']['bool']['must']

        # Filters:
        # [{'term': {'type': 1}},
        #  {'should': [{'term': {'status': 1}}, {'term': {'app': 2}}]}]
        assert len(filters) == 2
        assert {'term': {'type': 1}} in filters
        should_clause = sorted(filters)[0]
        assert should_clause.keys() == ['should']
        assert {'term': {'status': 1}} in should_clause['should']
        assert {'term': {'app': 2}} in should_clause['should']

    def test_source(self):
        qs = Addon.search().source('versions')
        assert qs._build_query()['_source'] == ['id', 'versions']

    def test_score(self):
        qs = Addon.search().score({'foo': 'bar'})
        query = qs._build_query()['query']
        assert 'function_score' in query
        assert len(query['function_score']['functions']) == 2
        assert query['function_score']['functions'][0] == {
            'field_value_factor': {'field': 'boost', 'missing': 1.0}}
        assert query['function_score']['functions'][1] == {'foo': 'bar'}


class TestES(ESTestCaseWithAddons):
    def test_getitem(self):
        addons = list(Addon.search())
        assert addons[0] == Addon.search()[0]

    def test_iter(self):
        qs = Addon.search().filter(type=1, is_disabled=False)
        assert len(qs) == len(list(qs))

    def test_count(self):
        assert Addon.search().count() == 6

    def test_count_uses_cached_results(self):
        qs = Addon.search()
        qs._results_cache = mock.Mock()
        qs._results_cache.count = mock.sentinel.count
        assert qs.count() == mock.sentinel.count

    def test_len(self):
        qs = Addon.search()
        qs._results_cache = [1]
        assert len(qs) == 1

    def test_values_result(self):
        addons = [{'id': a.id, 'slug': a.slug} for a in self._addons]
        qs = Addon.search().values_dict('slug').order_by('id')
        assert list(qs) == addons

    def test_values_dict_result(self):
        addons = [{'id': a.id, 'slug': a.slug} for a in self._addons]
        qs = Addon.search().values_dict('slug').order_by('id')
        assert list(qs) == list(addons)

    def test_empty_values_dict_result(self):
        qs = Addon.search().values_dict()
        assert qs[0].keys() == ['id']

    def test_object_result(self):
        qs = Addon.search().filter(id=self._addons[0].id)[:1]
        assert self._addons[:1] == list(qs)

    def test_object_result_slice(self):
        addon = self._addons[0]
        qs = Addon.search().filter(id=addon.id)
        assert addon == qs[0]

    def test_extra_bad_key(self):
        with self.assertRaises(AssertionError):
            Addon.search().extra(x=1)

    def test_aggregations(self):
        Tag(tag_text='sky').save_tag(self._addons[0])
        Tag(tag_text='sky').save_tag(self._addons[1])
        Tag(tag_text='sky').save_tag(self._addons[2])
        Tag(tag_text='earth').save_tag(self._addons[0])
        Tag(tag_text='earth').save_tag(self._addons[1])
        Tag(tag_text='ocean').save_tag(self._addons[0])
        self.reindex(Addon)

        qs = Addon.search().aggregate(tags={'terms': {'field': 'tags'}})
        results = list(qs)
        assert len(results) == 6
        assert qs.aggregations == {
            u'tags': [
                {u'doc_count': 3, u'key': u'sky'},
                {u'doc_count': 2, u'key': u'earth'},
                {u'doc_count': 1, u'key': u'ocean'}]}


class TestPaginator(ESTestCaseWithAddons):

    def setUp(self):
        super(TestPaginator, self).setUp()
        self.request = request = mock.Mock()
        request.GET.get.return_value = 1
        request.GET.urlencode.return_value = ''
        request.path = ''

    def test_es_paginator(self):
        qs = Addon.search()
        pager = amo.utils.paginate(self.request, qs)
        assert isinstance(pager.paginator, amo.utils.ESPaginator)

    def test_validate_number(self):
        p = amo.utils.ESPaginator(Addon.search(), 20)
        # A bad number raises an exception.
        with self.assertRaises(paginator.PageNotAnInteger):
            p.page('a')

        # A large number is ignored.
        p.page(99)

    def test_count(self):
        p = amo.utils.ESPaginator(Addon.search(), 20)
        assert p._count is None
        p.page(1)
        assert p.count == Addon.search().count()
