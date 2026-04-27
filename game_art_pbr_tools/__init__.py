bl_info = {
    "name": "PBR贴图助手",
    "author": "Neo",
    "version": (2, 4, 0),
    "blender": (3, 4, 0),
    "location": "Node Editor > Sidebar > PBR Tool",
    "description": "PBR贴图连接、重命名、清理、导出与压缩工具（正式版）",
    "category": "Node",
}

import bpy

if "bpy" in locals():
    import importlib
    if "properties" in locals():
        importlib.reload(properties)
    if "utils" in locals():
        importlib.reload(utils)
    if "operators" in locals():
        importlib.reload(operators)
    if "ui" in locals():
        importlib.reload(ui)

from . import properties
from . import utils
from . import operators
from . import ui

classes = (
    properties.PBRV2ToolProperties,
    
    operators.NODE_OT_PBRConnectArrange,
    operators.NODE_OT_PBRCleanNodes,
    operators.NODE_OT_PBRRenameMaterial,
    operators.NODE_OT_PBRRenameMaterialFromObject,
    operators.NODE_OT_PBRRenameTextures,
    operators.NODE_OT_PBRResetNodes,
    operators.NODE_OT_PBRCompressTextures,
    operators.NODE_OT_PBRExportTextures,
    operators.NODE_OT_PBROpenExportFolder,
    operators.NODE_OT_PBRBatchFolderConnect,
    
    ui.NODE_PT_PBRMainPanel,
    ui.NODE_PT_PBRCorePanel,
    ui.NODE_PT_PBRRenamePanel,
    ui.NODE_PT_PBRPreviewPanel,
    ui.NODE_PT_PBRCompressPanel,
    ui.NODE_PT_PBRBatchPanel,
)

def register():
    # 原有的清理工具已被精简，确保与其他插件完美兼容
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pbr_v2_props = bpy.props.PointerProperty(type=properties.PBRV2ToolProperties)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.pbr_v2_props

if __name__ == "__main__":
    register()
