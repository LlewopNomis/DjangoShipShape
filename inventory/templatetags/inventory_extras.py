from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from inventory.models import format_quantity

register = template.Library()


@register.filter
def qty(value):
    """Render a decimal quantity without trailing zeros, e.g. 10.00 -> '10', 4.50 -> '4.5'."""
    if value is None:
        return ''
    return format_quantity(value)


# Three bars, narrowest-to-widest top-to-bottom. Flipped vertically via CSS
# (.sort-icon-desc) for the descending direction, so one SVG covers both.
_SORT_ICON_SVG = (
    '<svg width="12" height="10" viewBox="0 0 14 12" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<rect x="0" y="0" width="6" height="2" rx="1" fill="currentColor"/>'
    '<rect x="0" y="5" width="10" height="2" rx="1" fill="currentColor"/>'
    '<rect x="0" y="10" width="14" height="2" rx="1" fill="currentColor"/>'
    '</svg>'
)


@register.simple_tag(takes_context=True)
def sort_header(context, field, label):
    """A clickable column header that sorts a queryset by GET param 'sort'
    (e.g. 'name' / '-name'). Shows the bar-stack icon only on the active
    column, flipped to reflect ascending vs descending."""
    request = context['request']
    current = request.GET.get('sort', '')
    is_active = current.lstrip('-') == field
    descending = current.startswith('-')
    next_sort = f'-{field}' if (is_active and not descending) else field

    params = request.GET.copy()
    params['sort'] = next_sort
    params.pop('page', None)

    icon = ''
    if is_active:
        icon_class = 'sort-icon sort-icon-desc' if descending else 'sort-icon'
        icon = format_html('<span class="{}">{}</span>', icon_class, mark_safe(_SORT_ICON_SVG))

    return format_html('<a href="?{}" class="sort-header">{} {}</a>', params.urlencode(), label, icon)


@register.simple_tag(takes_context=True)
def nav_is_active(context, *prefixes):
    """True if the current view's url_name belongs to one of the given
    sections, e.g. nav_is_active('item', 'spare') matches item_list,
    item_detail, spare_edit, etc. Lets a nav link stay highlighted on every
    page within its section, not just its own list page."""
    request = context['request']
    match = request.resolver_match
    if not match or not match.url_name:
        return False
    name = match.url_name
    return any(name == p or name.startswith(p + '_') for p in prefixes)


@register.simple_tag
def item_thumb(item):
    """A small thumbnail for an InventoryItem's first photo (primary photo
    first, per ItemPhoto's ordering) — a document icon for a PDF, a blank
    placeholder if it has none. Call item.photos.all() via prefetch_related
    upstream, or this becomes an N+1 query per row."""
    photo = next(iter(item.photos.all()), None)
    if photo is None:
        return mark_safe('<div class="photo-thumb-sm photo-thumb-placeholder"></div>')
    if photo.is_pdf:
        return mark_safe('<div class="photo-thumb-sm photo-thumb-sm-pdf">📄</div>')
    return format_html('<img src="{}" class="photo-thumb-sm" alt="">', photo.image.url)


@register.inclusion_tag('inventory/_tree_list.html')
def tree_list(nodes, detail_url_name, branch_icon='📁', leaf_icon='📦', default_depth=3):
    """Renders a treebeard node queryset (Location or ItemCategory) as a
    collapsible indented list: chevrons expand/collapse one node at a time,
    defaulting to `default_depth` levels open, plus a depth control that
    jumps every node open/closed to a given depth at once."""
    nodes = list(nodes)
    max_depth = max((n.depth for n in nodes), default=1)
    return {
        'nodes': nodes,
        'detail_url_name': detail_url_name,
        'branch_icon': branch_icon,
        'leaf_icon': leaf_icon,
        'default_depth': min(default_depth, max_depth),
        'max_depth': max_depth,
    }
