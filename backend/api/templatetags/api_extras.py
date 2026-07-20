from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Allow dict access with keys containing spaces or hyphens in templates."""
    return dictionary.get(key)
