import bpy
import os
import re
import tempfile

# --- 全局配置 ---
SUFFIX_MAP = {
    'BASE_COLOR': ['_d', '_bc', '_basecolor', '_albedo', '_diff', '_color'],
    'ORM': ['_arm', '_orm', '_ao_r_m'], # G=Roughness, B=Metallic
    'RMA': ['_rma'],                    # R=Roughness, G=Metallic
    'NORMAL': ['_n', '_nor', '_normal', '_nrm'],
    'ROUGHNESS': ['_r', '_roughness', '_rough', '_rgh'],
    'METALLIC': ['_m', '_metallic', '_metal', '_met'],
    'AO': ['_ao', '_ambientocclusion', '_occlusion']
}

def get_clean_name_for_search(image_name):
    name = image_name
    if len(name) > 4 and name[-4] == '.' and name[-3:].isdigit():
        name = name[:-4]
    name = os.path.splitext(name)[0]
    return name.lower()

def clean_name_for_rename(image_name):
    name = image_name
    if len(name) > 4 and name[-4] == '.' and name[-3:].isdigit():
        name = name[:-4]
    base, ext = os.path.splitext(name)
    if ext.lower() in ['.png', '.jpg', '.jpeg', '.tga', '.exr', '.tif', '.tiff', '.bmp', '.psd']:
        name = base
    return name

def check_suffix(clean_name_lower, suffixes):
    for s in suffixes:
        if clean_name_lower.endswith(s):
            return s
    return None


def match_orm_filename_suffix(name_lower):
    """
    ORM 贴图文件名：标准后缀或 glTF 导入常见的 _orm_0、_orm_12（与 _AO 命名成对替换时同一套基底）。
    """
    s = check_suffix(name_lower, SUFFIX_MAP['ORM'])
    if s:
        return s
    m = re.search(r'_orm_\d+$', name_lower)
    if m:
        return m.group(0)
    return None


def is_pbr_texture_name_lower(name_lower):
    """是否属于本工具识别的 PBR 贴图命名（含 glTF 的 _orm_N）。"""
    if any(check_suffix(name_lower, sfx) for sfx in SUFFIX_MAP.values()):
        return True
    return match_orm_filename_suffix(name_lower) is not None


def _force_claim_name(datablocks, current, target_name):
    """
    强制夺取命名权：
    - 若 datablocks 中已存在同名 target_name，且不是 current 本体，则把占位者改名为 target_name + "_old"
    - 再将 current.name 设置为 target_name
    说明：若 _old 也冲突，Blender 会自动追加 .001 等后缀，这里允许。
    """
    if not current or not target_name:
        return False

    existing = datablocks.get(target_name)
    if existing and existing != current:
        try:
            existing.name = target_name + "_old"
        except Exception:
            pass

    try:
        current.name = target_name
        return True
    except Exception:
        return False

def connect_pbr_nodes(nodes, links, principled_node, image_nodes):
    """将图像节点连接到 Principled BSDF 对应通道，并自动修正已有节点和色彩空间"""

    for img_node in image_nodes:
        if not img_node.image:
            continue
        
        name_lower = get_clean_name_for_search(img_node.image.name)

        if check_suffix(name_lower, SUFFIX_MAP['BASE_COLOR']):
            try: img_node.image.colorspace_settings.name = 'sRGB'
            except: pass
            
            target_socket = principled_node.inputs.get('Base Color')
            mix_node = None
            if target_socket and target_socket.is_linked:
                conn_node = target_socket.links[0].from_node
                if conn_node.type in ('MIX_RGB', 'MIX') and getattr(conn_node, 'blend_type', '') == 'MULTIPLY':
                    mix_node = conn_node
            
            if mix_node:
                in1 = mix_node.inputs.get('Color1') or mix_node.inputs.get('A')
                if not in1 and len(mix_node.inputs) > 1: in1 = mix_node.inputs[1]
                if in1: links.new(img_node.outputs['Color'], in1)
            elif target_socket:
                links.new(img_node.outputs['Color'], target_socket)

            # 团队规范：_D（基础色）不再自动连接 Alpha，避免影响 GLB 导出。
            # 如需透明效果，由美术手动判定并连接。

        elif match_orm_filename_suffix(name_lower):
            try: img_node.image.colorspace_settings.name = 'Non-Color'
            except: pass
            sep_node = None
            rough_socket = principled_node.inputs.get('Roughness')
            if rough_socket and rough_socket.is_linked:
                for link in rough_socket.links:
                    if link.from_node.type == 'SEPARATE_COLOR':
                        sep_node = link.from_node
                        break
            
            if not sep_node:
                sep_node = nodes.new(type='ShaderNodeSeparateColor')
            
            links.new(img_node.outputs['Color'], sep_node.inputs['Color'])
            if 'Roughness' in principled_node.inputs:
                links.new(sep_node.outputs[1], principled_node.inputs['Roughness'])
            if 'Metallic' in principled_node.inputs:
                links.new(sep_node.outputs[2], principled_node.inputs['Metallic'])
            
            base_color_input = principled_node.inputs.get('Base Color')
            if base_color_input:
                mix_node = None
                if base_color_input.is_linked:
                    for link in base_color_input.links:
                        if link.from_node.type in ('MIX_RGB', 'MIX'):
                            if getattr(link.from_node, 'blend_type', '') == 'MULTIPLY':
                                mix_node = link.from_node
                                break
                
                if not mix_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 1.0
                    if base_color_input.is_linked:
                        existing_link = base_color_input.links[0]
                        in1 = mix_node.inputs.get('Color1') or mix_node.inputs.get('A') or mix_node.inputs[1]
                        links.new(existing_link.from_socket, in1)
                    links.new(mix_node.outputs['Color'], base_color_input)
                
                in2 = mix_node.inputs.get('Color2') or mix_node.inputs.get('B')
                if not in2 and len(mix_node.inputs) > 2: in2 = mix_node.inputs[2]
                if in2: links.new(sep_node.outputs[0], in2)

        elif check_suffix(name_lower, SUFFIX_MAP['RMA']):
            try: img_node.image.colorspace_settings.name = 'Non-Color'
            except: pass
            sep_node = None
            rough_socket = principled_node.inputs.get('Roughness')
            if rough_socket and rough_socket.is_linked:
                for link in rough_socket.links:
                    if link.from_node.type == 'SEPARATE_COLOR':
                        sep_node = link.from_node
                        break
            if not sep_node:
                sep_node = nodes.new(type='ShaderNodeSeparateColor')
            
            links.new(img_node.outputs['Color'], sep_node.inputs['Color'])
            if 'Roughness' in principled_node.inputs:
                links.new(sep_node.outputs[0], principled_node.inputs['Roughness'])
            if 'Metallic' in principled_node.inputs:
                links.new(sep_node.outputs[1], principled_node.inputs['Metallic'])

        elif check_suffix(name_lower, SUFFIX_MAP['NORMAL']):
            try: img_node.image.colorspace_settings.name = 'Non-Color'
            except: pass
            
            nrm_node = None
            normal_socket = principled_node.inputs.get('Normal')
            if normal_socket and normal_socket.is_linked:
                for link in normal_socket.links:
                    if link.from_node.type == 'NORMAL_MAP':
                        nrm_node = link.from_node
                        break
            
            if not nrm_node:
                nrm_node = nodes.new(type='ShaderNodeNormalMap')
            
            links.new(img_node.outputs['Color'], nrm_node.inputs['Color'])
            if normal_socket:
                links.new(nrm_node.outputs['Normal'], normal_socket)

        elif check_suffix(name_lower, SUFFIX_MAP['ROUGHNESS']):
            try: img_node.image.colorspace_settings.name = 'Non-Color'
            except: pass
            target_socket = principled_node.inputs.get('Roughness')
            if target_socket:
                links.new(img_node.outputs['Color'], target_socket)

        elif check_suffix(name_lower, SUFFIX_MAP['METALLIC']):
            try: img_node.image.colorspace_settings.name = 'Non-Color'
            except: pass
            target_socket = principled_node.inputs.get('Metallic')
            if target_socket:
                links.new(img_node.outputs['Color'], target_socket)

        elif check_suffix(name_lower, SUFFIX_MAP['AO']):
            try: img_node.image.colorspace_settings.name = 'Non-Color'
            except: pass
            base_color_input = principled_node.inputs.get('Base Color')
            if base_color_input:
                mix_node = None
                if base_color_input.is_linked:
                    for link in base_color_input.links:
                        if link.from_node.type in ('MIX_RGB', 'MIX'):
                            if getattr(link.from_node, 'blend_type', '') == 'MULTIPLY':
                                mix_node = link.from_node
                                break
                
                if not mix_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 1.0
                    if base_color_input.is_linked:
                        existing_link = base_color_input.links[0]
                        in1 = mix_node.inputs.get('Color1') or mix_node.inputs.get('A') or mix_node.inputs[1]
                        links.new(existing_link.from_socket, in1)
                    links.new(mix_node.outputs['Color'], base_color_input)
                
                in2 = mix_node.inputs.get('Color2') or mix_node.inputs.get('B')
                if not in2 and len(mix_node.inputs) > 2: in2 = mix_node.inputs[2]
                if in2: links.new(img_node.outputs['Color'], in2)


def get_target_materials(context, scope):
    materials = []
    if scope == 'CURRENT':
        obj = context.active_object
        if obj and obj.active_material:
            materials.append(obj.active_material)
    elif scope == 'ALL':
        selected_objs = context.selected_objects
        processed = set()
        for obj in selected_objs:
            if not hasattr(obj, "material_slots"): continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat and mat not in processed:
                    materials.append(mat)
                    processed.add(mat)
    return materials


def get_compress_target_materials(context, scope):
    """
    压缩专用材质范围（与「自动连接」的 CURRENT 区分）：
    - CURRENT：活动物体上所有材质槽（多材质会全部处理）
    - ALL：所选物体全部材质槽（去重）
    """
    materials = []
    if scope == 'CURRENT':
        obj = context.active_object
        if obj and getattr(obj, "material_slots", None):
            seen = set()
            for slot in obj.material_slots:
                m = slot.material
                if m and m not in seen:
                    seen.add(m)
                    materials.append(m)
        if not materials and obj and getattr(obj, "active_material", None):
            materials.append(obj.active_material)
    elif scope == 'ALL':
        seen = set()
        for obj in context.selected_objects or []:
            if not obj or not hasattr(obj, "material_slots"):
                continue
            for slot in obj.material_slots:
                m = slot.material
                if m and m not in seen:
                    seen.add(m)
                    materials.append(m)
    return materials


def collect_images_from_materials(materials):
    """收集材质节点树内所有 Image Texture 节点引用的 bpy.data.images。"""
    images = set()
    for mat in materials or []:
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and getattr(node, "image", None):
                images.add(node.image)
    return images


def ensure_image_pixels_loaded(image):
    """
    Image.scale / 写盘依赖像素缓冲已载入内存。
    外部 FILE 且 has_data 为假时，从磁盘 reload。
    """
    if not image:
        return False
    if image.size[0] == 0 or image.size[1] == 0:
        return False
    if getattr(image, "has_data", False):
        return True
    try:
        if image.source == 'FILE' and image.filepath:
            abs_path = bpy.path.abspath(image.filepath)
            if os.path.isfile(abs_path):
                image.reload()
                return bool(getattr(image, "has_data", False))
    except Exception:
        pass
    return bool(getattr(image, "has_data", False))


def save_image_to_path_atomic(image, final_abs_path: str) -> bool:
    """
    将当前内存中的 Image 写入磁盘：先写入同目录临时文件，再 os.replace 覆盖目标。
    避免 Windows 下对正在读写的源文件原地 save() 失败或写盘后预览仍显示旧尺寸。
    """
    if not image or not final_abs_path:
        return False
    directory = os.path.dirname(final_abs_path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass

    old_filepath = image.filepath
    old_raw = getattr(image, "filepath_raw", None)
    target_ext = os.path.splitext(final_abs_path)[1] or ".png"
    fd, tmp_path = tempfile.mkstemp(prefix=".pbr_save_", suffix=target_ext, dir=directory)
    os.close(fd)
    target_size = (int(image.size[0]), int(image.size[1]))
    try:
        image.filepath = tmp_path
        if old_raw is not None:
            image.filepath_raw = tmp_path
        image.save()
        os.replace(tmp_path, final_abs_path)
        tmp_path = None
        image.filepath = final_abs_path
        if old_raw is not None:
            image.filepath_raw = image.filepath
        try:
            image.reload()
        except Exception:
            pass
        # 强校验：磁盘尺寸应与当前内存尺寸一致
        disk_size = read_disk_image_size(final_abs_path)
        if disk_size == target_size:
            return True

        # 回退：直接覆盖目标路径再写一次（避免某些环境下临时替换后尺寸未更新）
        try:
            image.filepath = final_abs_path
            if old_raw is not None:
                image.filepath_raw = final_abs_path
            image.save()
            try:
                image.reload()
            except Exception:
                pass
            disk_size = read_disk_image_size(final_abs_path)
            return disk_size == target_size
        except Exception as e:
            print(f"[PBR Tools] 回退直接写盘失败 {getattr(image, 'name', '?')}: {e}")
            return False
    except Exception as e:
        print(f"[PBR Tools] 原子保存失败 {getattr(image, 'name', '?')}: {e}")
        try:
            image.filepath = old_filepath
            if old_raw is not None:
                image.filepath_raw = old_raw
        except Exception:
            pass
        return False
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def save_image_to_path_verified(image, final_abs_path: str, scene=None) -> bool:
    """
    写盘（稳定优先）：
    1) 直接 save 到目标路径（最贴近当前 Image 数据写出）；
    2) 失败时回退 save_render；
    3) 再失败才尝试原子替换。
    """
    if not image or not final_abs_path:
        return False
    directory = os.path.dirname(final_abs_path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass

    old_filepath = image.filepath
    old_raw = getattr(image, "filepath_raw", None)

    # 路径 1（优先）：直接 save 到目标路径
    try:
        image.filepath = final_abs_path
        if old_raw is not None:
            image.filepath_raw = final_abs_path
        image.save()
        image.filepath = final_abs_path
        if old_raw is not None:
            image.filepath_raw = final_abs_path
        try:
            image.reload()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[PBR Tools] 直接 save 失败 {getattr(image, 'name', '?')}: {e}")
    finally:
        try:
            image.filepath = old_filepath
            if old_raw is not None:
                image.filepath_raw = old_raw
        except Exception:
            pass

    # 路径 2：save_render 回退
    try:
        image.save_render(final_abs_path, scene=scene)
        image.filepath = final_abs_path
        if old_raw is not None:
            image.filepath_raw = final_abs_path
        try:
            image.reload()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[PBR Tools] save_render 回退失败 {getattr(image, 'name', '?')}: {e}")
    finally:
        try:
            image.filepath = old_filepath
            if old_raw is not None:
                image.filepath_raw = old_raw
        except Exception:
            pass

    # 路径 3：原子替换兜底
    return save_image_to_path_atomic(image, final_abs_path)


def read_disk_image_size(abs_path: str):
    """
    读取磁盘图片尺寸，不影响现有 Image datablock。
    返回 (w, h) 或 None。
    """
    if not abs_path or not os.path.isfile(abs_path):
        return None
    tmp_img = None
    try:
        tmp_img = bpy.data.images.load(abs_path, check_existing=False)
        w, h = int(tmp_img.size[0]), int(tmp_img.size[1])
        if w <= 0 or h <= 0:
            return None
        return (w, h)
    except Exception:
        return None
    finally:
        if tmp_img is not None:
            try:
                bpy.data.images.remove(tmp_img)
            except Exception:
                pass


def find_images_recursive(socket, found_images, visited_nodes):
    if not socket.is_linked:
        return
    for link in socket.links:
        node = link.from_node
        if node in visited_nodes:
            continue
        visited_nodes.add(node)
        if node.type == 'TEX_IMAGE' and node.image:
            found_images.add(node.image)
        else:
            for input_socket in node.inputs:
                if input_socket.is_linked:
                    find_images_recursive(input_socket, found_images, visited_nodes)

def process_reset_material(mat):
    if not mat:
        return False
    
    # 确保启用节点系统（Blender 会自动创建 node_tree）
    if not mat.use_nodes:
        mat.use_nodes = True
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    output_node = None
    principled_node = None
    nodes_to_remove = []

    for n in nodes:
        if n.type == 'OUTPUT_MATERIAL' and output_node is None:
            output_node = n
        elif n.type == 'BSDF_PRINCIPLED' and principled_node is None:
            principled_node = n
        else:
            nodes_to_remove.append(n)
    
    for n in nodes_to_remove:
        nodes.remove(n)
    
    if not output_node:
        output_node = nodes.new('ShaderNodeOutputMaterial')
    if not principled_node:
        principled_node = nodes.new('ShaderNodeBsdfPrincipled')
    
    principled_node.location = (0, 0)
    output_node.location = (300, 0)
    
    is_connected = False
    if output_node.inputs['Surface'].is_linked:
        for link in output_node.inputs['Surface'].links:
            if link.from_node == principled_node:
                is_connected = True
                break
    
    if not is_connected:
        links.new(principled_node.outputs[0], output_node.inputs['Surface'])
    return True

def process_connect_and_arrange(mat, context=None):
    if not mat or not mat.use_nodes: return False
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    
    principled_node = None
    image_nodes = []
    
    for n in nodes:
        if n.type == 'BSDF_PRINCIPLED':
            principled_node = n
        elif n.type == 'TEX_IMAGE':
            image_nodes.append(n)
            
    if not principled_node:
        return False
        
    if not image_nodes:
        return True
        
    # 自动修正模式下不再盲目断开所有连接，而是由 connect_pbr_nodes 决定
    # 但如果是完全乱连的情况，用户执行“自动连接”仍期望重新建立标准连接
    # 为了满足“一键修正”，我们保留对 image_node 的重新连接，但 connect_pbr_nodes 内部变得聪明了
    for img_node in image_nodes:
        if not img_node.image: continue
        name_lower = get_clean_name_for_search(img_node.image.name)
        # 仅当它是我们需要管理的 PBR 贴图时，才由我们接管其连接
        is_pbr = is_pbr_texture_name_lower(name_lower)
        if is_pbr:
            for out in img_node.outputs:
                if out.is_linked:
                    for link in list(out.links):
                        # 如果连接的目标不是 Principled BSDF 或相关的中间节点，通常说明连错了，断开它
                        links.remove(link)

    
    connect_pbr_nodes(nodes, links, principled_node, image_nodes)
    
    # 调整贴图节点的垂直分布
    # 增加间距以避免重叠，同时处理不同贴图类型的默认排列顺序
    base_y = principled_node.location.y + 300
    y_spacing = 300  # 每张贴图之间的垂直间距 (高度)
    
    loc_y_map = {
        'BASE_COLOR': base_y,
        'METALLIC': base_y - y_spacing * 1,
        'ROUGHNESS': base_y - y_spacing * 2,
        'ORM': base_y - y_spacing * 2,
        'RMA': base_y - y_spacing * 2,
        'NORMAL': base_y - y_spacing * 3,
        'AO': base_y - y_spacing * 4
    }
    
    intermediate_x = principled_node.location.x - 260
    base_x = principled_node.location.x - 560
    
    # 排列中间节点（如果存在）
    nrm_socket = principled_node.inputs.get('Normal')
    if nrm_socket and nrm_socket.is_linked:
        nrm_node = nrm_socket.links[0].from_node
        if nrm_node.type == 'NORMAL_MAP':
            nrm_node.location = (intermediate_x, loc_y_map['NORMAL'])
            
    rough_socket = principled_node.inputs.get('Roughness')
    if rough_socket and rough_socket.is_linked:
        sep_node = rough_socket.links[0].from_node
        if sep_node.type == 'SEPARATE_COLOR':
            sep_node.location = (intermediate_x, loc_y_map['ORM'])
            
    bc_socket = principled_node.inputs.get('Base Color')
    if bc_socket and bc_socket.is_linked:
        mix_node = bc_socket.links[0].from_node
        if mix_node.type in ('MIX_RGB', 'MIX'):
            mix_node.location = (intermediate_x, loc_y_map['BASE_COLOR'])
            
    # 排列贴图节点
    for img_node in image_nodes:
        if not img_node.image: continue
        name_lower = get_clean_name_for_search(img_node.image.name)
        
        # 判断当前贴图属于什么类型
        pos_set = False
        img_node.location.x = base_x
        for type_key, suffixes in SUFFIX_MAP.items():
            if type_key == 'ORM':
                matched = match_orm_filename_suffix(name_lower)
            else:
                matched = check_suffix(name_lower, suffixes)
            if matched:
                img_node.location.y = loc_y_map.get(type_key, principled_node.location.y)
                pos_set = True
                break
                
        if not pos_set:
            img_node.location.y = principled_node.location.y - y_spacing * 5
            
    # --- 调用 Node Arrange 插件 (如果可用) ---
    if context:
        try:
            if hasattr(bpy.ops.node, "button"):
                area = next((a for a in context.screen.areas if a.type == 'NODE_EDITOR'), None)
                if area:
                    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                    if region:
                        # 对于不同 Blender 版本，temp_override 可能是需要 dict 或者 **kwargs
                        # 我们先强制将当前活动对象的材质切换为正在处理的 mat
                        obj = context.active_object
                        old_idx = -1
                        if obj and hasattr(obj, 'material_slots'):
                            old_idx = obj.active_material_index
                            for i, slot in enumerate(obj.material_slots):
                                if slot.material == mat:
                                    obj.active_material_index = i
                                    break
                        
                        # Node Arrange 会重排 active material, 所以上面的切换很重要
                        if bpy.app.version >= (3, 2, 0):
                            with context.temp_override(area=area, region=region):
                                bpy.ops.node.button()
                        else:
                            override = context.copy()
                            override['area'] = area
                            override['region'] = region
                            bpy.ops.node.button(override)
                            
                        if obj and old_idx >= 0:
                            obj.active_material_index = old_idx
        except Exception as e:
            print(f"[PBR Tools] 自动调用 Node Arrange 失败: {e}")
            
    return True

def process_clean_unused_nodes(mat):
    if not mat or not mat.use_nodes: return False
    
    nodes = mat.node_tree.nodes
    outputs = [n for n in nodes if n.type == 'OUTPUT_MATERIAL']
    if not outputs:
        return False
        
    visited = set()
    
    def traverse_backwards(node):
        if node in visited:
            return
        visited.add(node)
        for input_socket in node.inputs:
            if input_socket.is_linked:
                for link in input_socket.links:
                    if link.from_node:
                        traverse_backwards(link.from_node)
                        
    for out_node in outputs:
        traverse_backwards(out_node)
        
    to_remove = [n for n in nodes if n not in visited]
    
    for n in to_remove:
        nodes.remove(n)
        
    return len(to_remove) > 0

def process_rename_material(mat):
    if not mat or not mat.use_nodes: return False
    nodes = mat.node_tree.nodes
    
    image_nodes = [n for n in nodes if n.type == 'TEX_IMAGE']
    if not image_nodes: return False

    new_material_name = None
    best_priority = -1 

    for img_node in image_nodes:
        if not img_node.image:
            continue
        
        raw_clean_name = clean_name_for_rename(img_node.image.name)
        name_lower = raw_clean_name.lower()
        
        matched_suffix = None
        current_priority = 0
        
        s = check_suffix(name_lower, SUFFIX_MAP['BASE_COLOR'])
        if s:
            matched_suffix = s
            current_priority = 2
        
        if not matched_suffix:
            orm_s = match_orm_filename_suffix(name_lower)
            if orm_s:
                matched_suffix = orm_s
                current_priority = 1
            else:
                for key in ['RMA', 'NORMAL']:
                    s = check_suffix(name_lower, SUFFIX_MAP[key])
                    if s:
                        matched_suffix = s
                        current_priority = 1
                        break
        
        if matched_suffix and current_priority > best_priority:
            suffix_len = len(matched_suffix)
            core_name = raw_clean_name[:-suffix_len]
            
            if core_name.upper().startswith("T_"):
                final_name = "M_" + core_name[2:]
            elif core_name.upper().startswith("T"):
                final_name = "M" + core_name[1:]
            else:
                final_name = "M_" + core_name
            
            new_material_name = final_name
            best_priority = current_priority

    if new_material_name and mat.name != new_material_name:
        _force_claim_name(bpy.data.materials, mat, new_material_name)
        return True
        
    return False


def process_rename_materials_from_object(obj):
    """
    根据物体名重命名其材质槽中的材质：
    - 物体名前缀：SM_ / sm_ -> M_（去掉 SM_，加 M_）
    - 无 SM_ 前缀：在前面加 M_
    - 单材质：直接用目标名称
    - 多材质：按槽位顺序追加大写字母 A/B/C...：M_xxx_A/B/C...
    返回值：成功重命名的材质数量
    """
    if not obj or not hasattr(obj, "material_slots"):
        return 0

    # 收集非空材质槽并保持顺序
    non_empty_slots = []
    for slot in obj.material_slots:
        if getattr(slot, "material", None) is not None:
            non_empty_slots.append(slot)

    if not non_empty_slots:
        return 0

    obj_name = obj.name
    if obj_name.lower().startswith("sm_"):
        core_name = obj_name[3:]  # 去掉 SM_
        target_base = "M_" + core_name
    else:
        target_base = "M_" + obj_name

    renamed = 0
    if len(non_empty_slots) == 1:
        target_name = target_base
        mat = non_empty_slots[0].material
        if mat and mat.name != target_name:
            if _force_claim_name(bpy.data.materials, mat, target_name):
                renamed = 1
        return renamed

    def _indexed_slot_suffix(index: int) -> str:
        """
        后缀规则：
        - 第 1 轮：A ~ Z
        - 第 2 轮：A1 ~ Z1
        - 第 3 轮：A2 ~ Z2
        """
        letter = chr(65 + (index % 26))  # 65='A'
        round_idx = index // 26
        if round_idx == 0:
            return letter
        return f"{letter}{round_idx}"

    # 多材质：按槽位顺序追加后缀，支持超出 Z 的连续编号
    for idx, slot in enumerate(non_empty_slots):
        suffix = _indexed_slot_suffix(idx)
        target_name = f"{target_base}_{suffix}"
        mat = slot.material
        if mat and mat.name != target_name:
            if _force_claim_name(bpy.data.materials, mat, target_name):
                renamed += 1

    return renamed


def _build_texture_base_name_from_material_name(material_name):
    """将材质名转换为贴图基础名：M_xxx -> T_xxx；否则前置 T_。"""
    if not material_name:
        return "T_Untitled"
    if material_name[:2].lower() == "m_":
        return "T_" + material_name[2:]
    return "T_" + material_name


def _image_extension(image):
    """获取真实的图像扩展名，彻底忽略 Blender 的 .001 命名后缀。优先从 filepath 获取，其次从 file_format 推断。"""
    # 1. 优先尝试从真实磁盘路径获取扩展名
    if image.filepath:
        ext = os.path.splitext(bpy.path.abspath(image.filepath))[1].lower()
        valid_exts = ['.png', '.jpg', '.jpeg', '.tga', '.exr', '.tif', '.tiff', '.bmp', '.psd']
        if ext in valid_exts:
            return ext
            
    # 2. 如果路径为空或扩展名不规范（例如打包的纯内嵌/生成的贴图），通过 Blender 内部的 file_format 推断
    format_ext_map = {
        'JPEG': '.jpg', 'PNG': '.png', 'TARGA': '.tga', 'TARGA_RAW': '.tga',
        'TIFF': '.tif', 'BMP': '.bmp', 'OPEN_EXR': '.exr'
    }
    if image.file_format in format_ext_map:
        return format_ext_map[image.file_format]
        
    # 3. 兜底方案
    return ".png"


def _make_link_key(link):
    try:
        return (
            link.from_node.name,
            link.from_socket.identifier,
            link.to_node.name,
            link.to_socket.identifier,
        )
    except Exception:
        return (id(link),)


def _is_socket_reaching_principled_input(output_socket, input_name, visited_links):
    """
    递归判断某个输出 socket 是否最终连到 Principled 的指定输入。
    """
    for link in output_socket.links:
        link_key = _make_link_key(link)
        if link_key in visited_links:
            continue
        visited_links.add(link_key)

        to_node = link.to_node
        to_socket = link.to_socket

        if to_node.type == 'BSDF_PRINCIPLED' and to_socket.name == input_name:
            return True

        if to_node.type == 'NORMAL_MAP' and to_socket.name == 'Color':
            normal_out = to_node.outputs.get('Normal')
            if normal_out and _is_socket_reaching_principled_input(normal_out, 'Normal', visited_links):
                return True

        for out_socket in to_node.outputs:
            if out_socket.is_linked and _is_socket_reaching_principled_input(out_socket, input_name, visited_links):
                return True
    return False


def _is_socket_reaching_gltf_occlusion(output_socket, visited_links):
    """
    递归判断输出是否最终连到 glTF Material Output（或同类）的 Occlusion 槽，
    与 GLB 导入常见的 ORM：Separate Color 的 Red → Occlusion 一致。
    """
    for link in output_socket.links:
        link_key = _make_link_key(link)
        if link_key in visited_links:
            continue
        visited_links.add(link_key)

        to_node = link.to_node
        to_socket = link.to_socket

        if to_socket and to_socket.name == 'Occlusion':
            return True

        for out_sock in to_node.outputs:
            if out_sock.is_linked and _is_socket_reaching_gltf_occlusion(out_sock, visited_links):
                return True
    return False


def _trace_usage_suffixes_from_socket(output_socket, visited_links):
    """
    从图像节点某个输出出发，追踪最终用途并返回命名后缀集合。
    """
    found = set()
    for link in output_socket.links:
        link_key = _make_link_key(link)
        if link_key in visited_links:
            continue
        visited_links.add(link_key)

        to_node = link.to_node
        to_socket = link.to_socket

        # 直接连 Principled 通道
        if to_node.type == 'BSDF_PRINCIPLED':
            if to_socket.name == 'Base Color':
                found.add('_D')
            elif to_socket.name == 'Roughness':
                found.add('_R')
            elif to_socket.name == 'Metallic':
                found.add('_M')
            continue

        if to_socket and to_socket.name == 'Occlusion':
            found.add('_AO')
            continue

        # 通过 Normal Map 连 Principled.Normal
        if to_node.type == 'NORMAL_MAP' and to_socket.name == 'Color':
            normal_out = to_node.outputs.get('Normal')
            if normal_out:
                if _is_socket_reaching_principled_input(normal_out, 'Normal', set(visited_links)):
                    found.add('_N')
            continue

        # Separate Color：绿→Roughness 且 蓝→Metallic 时视为完整 ORM，命名始终 _ORM（红仍可接 Occlusion）。
        # 仅当「只有红通道」接到外置 Occlusion（如 glTF Material Output）且未形成上述 ORM 时，才按 _AO 命名。
        if to_node.type == 'SEPARATE_COLOR' and to_socket.name == 'Color':
            has_rough = False
            has_metal = False
            for out_socket in to_node.outputs:
                if _is_socket_reaching_principled_input(out_socket, 'Roughness', set(visited_links)):
                    has_rough = True
                if _is_socket_reaching_principled_input(out_socket, 'Metallic', set(visited_links)):
                    has_metal = True
            red_out = to_node.outputs.get('Red')
            is_full_orm = has_rough and has_metal
            if is_full_orm:
                found.add('_ORM')
            elif red_out and _is_socket_reaching_gltf_occlusion(red_out, set(visited_links)):
                found.add('_AO')
            if not is_full_orm:
                if has_rough:
                    found.add('_R')
                if has_metal:
                    found.add('_M')
            continue

        # 其他中间节点：继续向后递归追踪
        for next_output in to_node.outputs:
            if next_output.is_linked:
                found.update(_trace_usage_suffixes_from_socket(next_output, visited_links))

    return found


def _image_needs_pixel_flush_to_disk(image, abs_path: str) -> bool:
    """
    判断当前内存中的 Image（含 scale 后分辨率）是否需覆盖写入磁盘文件。
    与压缩面板的 _pbr_unsynced_resize 标记及磁盘/内存尺寸比较一致。
    """
    if not image:
        return False
    if not abs_path or not os.path.isfile(abs_path):
        return bool(image.get("_pbr_unsynced_resize"))
    if not ensure_image_pixels_loaded(image):
        return bool(image.get("_pbr_unsynced_resize"))
    if image.get("_pbr_unsynced_resize"):
        return True
    ds = read_disk_image_size(abs_path)
    if not ds:
        return True
    w, h = int(image.size[0]), int(image.size[1])
    return ds[0] != w or ds[1] != h


def process_rename_textures_from_material(mat, props=None, scene=None):
    """
    根据材质球名称逆向重命名连接贴图。
    返回 (数据块重命名张数, 磁盘文件重命名次数, 将内存像素写回磁盘的张数)；
    未开启磁盘同步时后两项恒为 0。
    """
    if not mat or not mat.use_nodes or not mat.node_tree:
        return 0, 0, 0

    base_name = _build_texture_base_name_from_material_name(mat.name)
    image_nodes = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]
    if not image_nodes:
        return 0, 0, 0

    # 优先级：ORM > AO（仅红→外置 Occlusion 且无完整 ORM 时）> Normal > BaseColor > Roughness > Metallic
    suffix_priority = ['_ORM', '_AO', '_N', '_D', '_R', '_M']
    image_to_suffixes = {}

    for img_node in image_nodes:
        img = img_node.image
        usage_suffixes = set()

        color_out = img_node.outputs.get('Color')
        if color_out and color_out.is_linked:
            usage_suffixes.update(_trace_usage_suffixes_from_socket(color_out, set()))

        alpha_out = img_node.outputs.get('Alpha')
        if alpha_out and alpha_out.is_linked:
            usage_suffixes.update(_trace_usage_suffixes_from_socket(alpha_out, set()))

        if not usage_suffixes:
            continue

        if img not in image_to_suffixes:
            image_to_suffixes[img] = set()
        image_to_suffixes[img].update(usage_suffixes)

    renamed_count = 0
    disk_renamed_count = 0
    disk_pixels_flushed = 0
    for img, suffixes in image_to_suffixes.items():
        chosen_suffix = None
        for s in suffix_priority:
            if s in suffixes:
                chosen_suffix = s
                break
        if not chosen_suffix:
            continue

        ext = _image_extension(img)
        new_base_filename = f"{base_name}{chosen_suffix}"
        new_name = f"{new_base_filename}{ext}"
        if img.name != new_name:
            if _force_claim_name(bpy.data.images, img, new_name):
                renamed_count += 1

        # 可选：同步磁盘文件（重命名路径 + 将内存中当前像素/尺寸写回文件，继承压缩/缩放结果）
        if props is not None and getattr(props, "rename_disk_files", False):
            try:
                old_path = bpy.path.abspath(img.filepath) if img.filepath else ""
            except Exception:
                old_path = ""

            if not (old_path and os.path.isfile(old_path)):
                continue

            dir_path = os.path.dirname(old_path)
            target_filename = f"{new_base_filename}{ext}"
            target_path = os.path.join(dir_path, target_filename)

            final_path = target_path
            if os.path.exists(final_path) and os.path.normpath(final_path) != os.path.normpath(old_path):
                base_no_ext, _ = os.path.splitext(new_base_filename)
                idx = 1
                while True:
                    candidate_filename = f"{base_no_ext}_{idx}{ext}"
                    candidate_path = os.path.join(dir_path, candidate_filename)
                    if not os.path.exists(candidate_path):
                        final_path = candidate_path
                        break
                    idx += 1

            did_rename = os.path.normpath(final_path) != os.path.normpath(old_path)
            if did_rename:
                try:
                    os.rename(old_path, final_path)
                    img.filepath = final_path
                    raws = getattr(img, "filepath_raw", None)
                    if raws is not None:
                        img.filepath_raw = final_path
                    disk_renamed_count += 1
                except Exception as e:
                    print(f"[PBR Tools] 重命名磁盘贴图失败 {img.name}: {e}")
                    continue
            else:
                final_path = old_path

            # 将内存中当前贴图写回 final_path：覆盖「仅 os.rename 未改像素」与「压缩后未同步到磁盘」两种情况
            if _image_needs_pixel_flush_to_disk(img, final_path):
                if save_image_to_path_verified(img, final_path, scene):
                    disk_pixels_flushed += 1
                    try:
                        if "_pbr_unsynced_resize" in img:
                            del img["_pbr_unsynced_resize"]
                    except Exception:
                        pass

    return renamed_count, disk_renamed_count, disk_pixels_flushed
