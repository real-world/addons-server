import functools

from django import http
from django.shortcuts import get_object_or_404

import waffle

from addons.models import Addon


def addon_view(f, qs=Addon.objects.all):
    @functools.wraps(f)
    def wrapper(request, addon_id, *args, **kw):
        get = lambda **kw: get_object_or_404(qs(), **kw)
        if addon_id.isdigit():
            addon = get(id=addon_id)
            # Don't get in an infinite loop if addon.slug.isdigit().
            if addon.slug != addon_id:
                url = request.path.replace(addon_id, addon.slug)
                if request.GET:
                    url += '?' + request.GET.urlencode()
                return http.HttpResponsePermanentRedirect(url)
        else:
            addon = get(slug=addon_id)
        return f(request, addon, *args, **kw)
    return wrapper


def addon_view_factory(qs):
    # Don't evaluate qs or the locale will get stuck on whatever the server
    # starts with. The addon_view() decorator will call qs with no arguments
    # before doing anything, so lambdas are ok.
    # GOOD: Addon.objects.valid
    # GOOD: lambda: Addon.objects.valid().filter(type=1)
    # BAD: Addon.objects.valid()
    return functools.partial(addon_view, qs=qs)


def can_be_purchased(f):
    """
    Check if it can be purchased, returns False if not premium.
    Must be called after the addon_view decorator.
    """
    @functools.wraps(f)
    def wrapper(request, addon, *args, **kw):
        if not waffle.switch_is_active('marketplace'):
            raise http.Http404
        if not addon.can_be_purchased():
            return http.HttpResponseForbidden()
        return f(request, addon, *args, **kw)
    return wrapper


def has_purchased(f):
    """
    If the addon is premium, require a purchase.
    Must be called after addon_view decorator.
    """
    @functools.wraps(f)
    def wrapper(request, addon, *args, **kw):
        if addon.is_premium() and not addon.has_purchased(request.amo_user):
            return http.HttpResponseForbidden()
        return f(request, addon, *args, **kw)
    return wrapper
