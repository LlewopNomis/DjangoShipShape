from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from inventory.fitment import FITS, OTHER_BUILD, fitment_for
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
def multi_sort_header(context, field, label):
    """Like sort_header, but GET param 'sort' is a comma-separated list of
    columns (e.g. 'fig,-item_no'), primary first. A plain click sorts by this
    column alone (toggling direction if it already is the only sort); the
    data-sort-add URL, followed on shift-click, instead cycles this column
    within the list: add as the last tie-breaker -> flip to descending ->
    remove. Shows the column's position in the list when there's more than one.
    If the view sets 'effective_sort' (a default applied when ?sort= is
    absent), that is what the headers show and build on."""
    request = context['request']
    current = context.get('effective_sort') or request.GET.get('sort', '')
    terms = [t for t in current.split(',') if t]
    fields = [t.lstrip('-') for t in terms]

    def url(new_terms):
        params = request.GET.copy()
        if new_terms:
            params['sort'] = ','.join(new_terms)
        else:
            params.pop('sort', None)
        params.pop('page', None)
        return '?' + params.urlencode()

    click_terms = [f'-{field}'] if terms == [field] else [field]
    if field in fields:
        i = fields.index(field)
        if terms[i].startswith('-'):
            add_terms = terms[:i] + terms[i + 1:]
        else:
            add_terms = terms[:i] + [f'-{field}'] + terms[i + 1:]
    else:
        add_terms = terms + [field]

    icon = ''
    if field in fields:
        i = fields.index(field)
        icon_class = 'sort-icon sort-icon-desc' if terms[i].startswith('-') else 'sort-icon'
        rank = format_html('<sup class="sort-rank">{}</sup>', i + 1) if len(terms) > 1 else ''
        icon = format_html('<span class="{}">{}</span>{}', icon_class, mark_safe(_SORT_ICON_SVG), rank)

    return format_html(
        '<a href="{}" data-sort-add="{}" class="sort-header" '
        'title="Click to sort by this column; shift-click to add it as a further sort">{} {}</a>',
        url(click_terms), url(add_terms), label, icon,
    )


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
def tree_list(nodes, detail_url_name, branch_icon='📁', leaf_icon='📦', default_depth=3, count_param=''):
    """Renders a treebeard node queryset (Location or ItemCategory) as a
    collapsible indented list: chevrons expand/collapse one node at a time,
    defaulting to `default_depth` levels open, plus a depth control that
    jumps every node open/closed to a given depth at once.

    If the nodes carry an `.item_count` (see views.attach_subtree_item_counts),
    each row shows that count linked to the item list filtered on `count_param`
    ('location' or 'category') — so "how many, and what are they" is one click
    away instead of a separate preview feature."""
    nodes = list(nodes)
    max_depth = max((n.depth for n in nodes), default=1)
    return {
        'nodes': nodes,
        'detail_url_name': detail_url_name,
        'branch_icon': branch_icon,
        'leaf_icon': leaf_icon,
        'default_depth': min(default_depth, max_depth),
        'max_depth': max_depth,
        'count_param': count_param,
    }


def fit_text(fit, equipment):
    """Plain-English fit status, e.g. 'Other build for Main engine (E23123): from E25003'."""
    who = f'{equipment.name} ({equipment.serial})' if equipment.serial else equipment.name
    if fit.status == FITS:
        qty = f', qty {format_quantity(fit.quantity)}' if fit.quantity is not None else ''
        return f'Fits {who}{": " + fit.reason if fit.reason else ""}{qty}'
    if fit.status == OTHER_BUILD:
        return f'Other build for {who}: {fit.reason}'
    return f'Not sure it fits {who}: {fit.reason}'


@register.simple_tag
def fit_badge(fit, equipment):
    """✓ / ✗ / ? after a catalog part, with the reason on hover (and for
    screen readers). Empty when there's nothing to check against."""
    if fit is None or equipment is None:
        return ''
    symbol, colour = {FITS: ('✓', 'text-success'), OTHER_BUILD: ('✗', 'text-danger')}.get(
        fit.status, ('?', 'text-muted'),
    )
    text = fit_text(fit, equipment)
    return format_html(
        '<span class="fit-badge {}" title="{}" aria-hidden="true">{}</span><span class="visually-hidden">{}</span>',
        colour, text, symbol, text,
    )


@register.simple_tag
def fit_status(fit, equipment):
    """The fit spelled out as a line of text, e.g. on the requirement page."""
    colour = {FITS: 'text-success', OTHER_BUILD: 'text-danger'}.get(fit.status, 'text-muted')
    symbol = {FITS: '✓', OTHER_BUILD: '✗'}.get(fit.status, '?')
    return format_html('<p class="small fw-semibold mb-0 {}">{} {}</p>', colour, symbol, fit_text(fit, equipment))


_FIT_ORDER = {FITS: 0, OTHER_BUILD: 2}


@register.inclusion_tag('inventory/_catalog_part_field.html')
def catalog_part_field(field, equipment=None):
    """Renders a CatalogPartChoiceField (hidden ModelChoiceField widget) as a
    type-to-search box instead of a giant <select> — with 2000+ catalog parts,
    scrolling a dropdown to find one is unworkable, and matching by pasting a
    bare part number (with no description/section attached) needs to work too,
    since that's what's actually printed on the requirement/item you're filling
    in from. See _catalog_part_field.html for the matching logic. Given the
    equipment it's for (e.g. the job's engine), each part is marked ✓ / ✗
    against it, fitting parts listed first."""
    model_field = field.field
    queryset = model_field.queryset
    if equipment is not None and equipment.variant_id:
        queryset = queryset.prefetch_related('fitments')
    else:
        equipment = None
    options = []
    seen_labels = set()
    for obj in queryset:
        label = model_field.label_from_instance(obj)
        option = {'id': obj.pk, 'part_number': obj.part_number, 'rank': 1, 'fit': ''}
        if equipment is not None:
            # Flag, never hide: the boat may not match its book.
            fit = fitment_for(obj, equipment)
            option['rank'] = _FIT_ORDER.get(fit.status, 1)
            option['fit'] = fit_text(fit, equipment)
            label = {FITS: f'✓ {label}', OTHER_BUILD: f'✗ {label}'}.get(fit.status, label)
        # The picker maps label -> id, so labels must be unique; should two
        # lines ever still read the same, tell the later one apart by id.
        if label in seen_labels:
            label = f'{label} #{obj.pk}'
        seen_labels.add(label)
        option['label'] = label
        options.append(option)
    # Parts that fit first, then can't-tell, then other builds.
    options.sort(key=lambda o: o['rank'])
    selected_label = ''
    selected_id = field.value()
    if selected_id:
        selected = next((o for o in options if str(o['id']) == str(selected_id)), None)
        if selected:
            selected_label = selected['label']
    return {
        'field': field,
        'options': options,
        'equipment': equipment,
        'selected_label': selected_label,
        'data_id': f'{field.html_name}-catalog-part-data',
    }
