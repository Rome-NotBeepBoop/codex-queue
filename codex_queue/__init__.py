"""Reusable Codex scheduling infrastructure."""

# Safeguard Textual DOM component style queries during asynchronous mounts/screen transitions.
try:
    from textual.dom import DOMNode, RenderStyles
    from textual.css.styles import Styles

    _orig_get_component_styles = DOMNode.get_component_styles

    def _safe_get_component_styles(self, *names: str) -> RenderStyles:
        try:
            return _orig_get_component_styles(self, *names)
        except KeyError:
            styles = RenderStyles(self, Styles(), Styles())
            for name in names:
                if name in self._component_styles:
                    cs = self._component_styles[name]
                    styles._update_node(cs.node)
                    styles.base.merge(cs.base)
                    styles.inline.merge(cs.inline)
                    styles._updates += 1
                elif name in getattr(self, 'COMPONENT_CLASSES', ()):
                    pass
                else:
                    raise
            return styles

    DOMNode.get_component_styles = _safe_get_component_styles
except (ImportError, AttributeError):
    pass
