"""
VRM Avatar Module - Safe loading entry

Zero-intrusion principle: load failure does not affect main program.
"""

VRM_AVAILABLE = False
vrm_widget_class = None

try:
    from .vrm_widget import VRMWidget
    VRM_AVAILABLE = True
    vrm_widget_class = VRMWidget
except Exception as e:
    print(f"[VRM] Module load failed, skipped: {e}")
