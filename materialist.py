"""
Materialist - Material Manager for Autodesk Maya
================================================

A shelf tool for searching, assigning, transferring, duplicating, exporting,
and cleaning up materials / shading networks in a Maya scene.

Usage:
    import materialist
    materialist.show()

Compatibility: Maya 2022+ (Python 3).
Author: Ruxin Liang  -  https://www.behance.net/ruxin-liang
License: MIT
"""

import re

import maya.cmds as cmds
import maya.mel as mel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

__version__ = "1.1.0"

WINDOW_NAME = "materialistWindow"
# The version is shown in the title so a bug report identifies its build.
WINDOW_TITLE = "Materialist {} - Material Manager".format(__version__)

# Suffix used in the list to flag a material that has no shading group.
# Defined once so the same string is used when appending AND when stripping.
NO_SG_SUFFIX = " (*No SG)"

# Patterns for producing clean, prefix/suffix-free names for duplicated nodes:
# strip Maya's "pasted__" paste prefix and any trailing numbers.
_PASTED_PREFIX_PATTERN = re.compile(r"^(pasted_+|pasted)+", re.IGNORECASE)
_NUMERIC_SUFFIX_PATTERN = re.compile(r"(_\d+|\d+)$")

# inViewMessage status colors (integers in 0xRRGGBBAA form).
STATUS_OK = 0x2E7D32CC   # green
STATUS_ERR = 0xC62828CC  # red

# UI palette - warm amber (option 2): warm-grey base, dark readable input
# fields, a medium warm-amber accent for the primary actions, and a darker
# amber for the destructive Delete; everything else neutral.
COLOR_BG = (0.165, 0.157, 0.145)
COLOR_PANEL = (0.082, 0.075, 0.059)
COLOR_FIELD = (0.80, 0.53, 0.22)      # bright amber: editable fields / dropdowns (dark text via _style_field)
BTN_PRIMARY = (0.78, 0.50, 0.17)      # medium amber: list all / create and assign
BTN_ACTION = (0.33, 0.31, 0.285)      # warm neutral (brightened): most buttons
BTN_MATERIAL = (0.33, 0.31, 0.285)    # warm neutral (brightened): duplicate
BTN_SEARCH = (0.33, 0.31, 0.285)      # warm neutral (brightened): diagnostics searches
BTN_DANGER = (0.45, 0.285, 0.105)     # dark amber warning: delete material

# optionVar prefix used to remember each section's collapse state.
_OPTVAR_PREFIX = "materialist_collapse_"

# Holds the names of the UI controls once the window is built.
_ui = {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _msg(text, ok=True):
    """Show a transient in-view status message (and a warning on failure)."""
    try:
        cmds.inViewMessage(
            statusMessage=text,
            fade=True,
            position="midCenterTop",
            backColor=(STATUS_OK if ok else STATUS_ERR),
        )
    except Exception:
        pass
    if not ok:
        cmds.warning(text)


def _clean_name(name):
    """Strip the '(*No SG)' display tag to recover the real node name."""
    return name.replace(NO_SG_SUFFIX, "") if name else name


def _unique(items):
    """Return items in their original order with duplicates removed."""
    seen = set()
    unique = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _clean_shader_name(name):
    """Return a base name with the 'pasted__' prefix and trailing numbers removed."""
    short_name = name.split("|")[-1]
    cleaned = _PASTED_PREFIX_PATTERN.sub("", short_name)
    cleaned = _NUMERIC_SUFFIX_PATTERN.sub("", cleaned)
    return cleaned if cleaned else short_name


def _make_unique_name(base_name):
    """Return base_name, or base_name + smallest free index if it already exists."""
    if not cmds.objExists(base_name):
        return base_name
    index = 1
    while cmds.objExists("%s%d" % (base_name, index)):
        index += 1
    return "%s%d" % (base_name, index)


def _repeatable(func):
    """Run ``func`` and register it as Maya's repeat-last command.

    Works whether the tool is imported as a module or pasted into the script
    editor, because it repeats through this module's ``__name__``.
    """
    func()
    try:
        cmd = 'python("import {mod}; {mod}.{fn}()")'.format(mod=__name__, fn=func.__name__)
        cmds.repeatLast(addCommand=cmd, addCommandLabel=func.__name__)
    except Exception:
        pass


def _resolve_shading_engines():
    """Return the shading engine(s) to duplicate.

    Uses the Matching Materials list selection first; otherwise the current
    scene / Node Editor selection. Materials and shaders are resolved to their
    shading engine; a selected shading engine is used directly.
    """
    engines = []

    list_ctrl = _ui.get("material_list")
    if list_ctrl and cmds.textScrollList(list_ctrl, exists=True):
        selected = cmds.textScrollList(list_ctrl, query=True, selectItem=True)
        if selected:
            name = _clean_name(selected[0])
            if cmds.objExists(name):
                engines += cmds.listConnections(name, type="shadingEngine") or []

    if not engines:
        for node in (cmds.ls(selection=True) or []):
            if cmds.nodeType(node) == "shadingEngine":
                engines.append(node)
            elif cmds.ls(node, materials=True):
                engines += cmds.listConnections(node, type="shadingEngine") or []

    seen, unique = set(), []
    for eng in engines:
        if eng not in seen:
            seen.add(eng)
            unique.append(eng)
    return unique


# ---------------------------------------------------------------------------
# Query utilities
# ---------------------------------------------------------------------------

def find_materials(search_query, namespace, only_with_sg):
    search_query = search_query.lower()
    all_materials = cmds.ls(materials=True, long=True)

    if namespace == "Has no Namespace":
        all_materials = [mat for mat in all_materials if ":" not in mat]
    elif namespace != "All Namespaces":
        all_materials = [mat for mat in all_materials if mat.startswith(namespace + ":")]

    matching_materials = [mat for mat in all_materials if search_query in mat.lower()]

    if only_with_sg:
        matching_materials = [mat for mat in matching_materials if get_shading_group(mat)]

    return matching_materials


def get_shading_groups(material_name):
    """Return every shading group driven by a material."""
    try:
        connections = cmds.listConnections(
            material_name + ".outColor",
            source=False,
            destination=True,
            type="shadingEngine",
        ) or []
        return _unique(connections)
    except Exception:
        return []


def get_shading_group(material_name):
    """Return the first shading group driven by a material, if any."""
    shading_groups = get_shading_groups(material_name)
    return shading_groups[0] if shading_groups else None


def _materials_from_shading_engines(shading_engines):
    """Return all surface materials connected to the supplied shading groups."""
    materials = []
    for shading_engine in shading_engines:
        connected = cmds.listConnections(
            shading_engine + ".surfaceShader",
            source=True,
            destination=False,
        ) or []
        materials += cmds.ls(connected, materials=True, long=True) or []
    return _unique(materials)


def _node_shapes(node):
    """Return a node's non-intermediate shapes, or the node itself if it is one."""
    child_shapes = cmds.listRelatives(
        node,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
    ) or []
    if child_shapes:
        return child_shapes
    if cmds.nodeType(node) in ("mesh", "nurbsSurface", "subdiv"):
        return cmds.ls(node, long=True) or []
    return []


def _assigned_shading_engines(object_name):
    """Return the shading groups that currently hold the object or its faces.

    listHistory also reports shading groups that no longer contain anything of
    this object - initialShadingGroup survives in the history graph after every
    face has been reassigned - which would overstate the material count and
    send a single-material source down the per-face branch. listSets reports
    current membership only.
    """
    shading_engines = []
    for shape in _node_shapes(object_name):
        shading_engines += cmds.listSets(object=shape, type=1) or []
    return _unique(cmds.ls(shading_engines, type="shadingEngine") or [])


def _selected_shapes():
    """Return the non-intermediate shapes represented by the current selection."""
    selected_nodes = cmds.ls(
        selection=True,
        objectsOnly=True,
        long=True,
    ) or []
    shapes = []
    for node in selected_nodes:
        shapes += _node_shapes(node)
    return _unique(shapes)


def _build_material_entries(materials):
    """Return display strings for the list, tagging materials that have no
    shading group with '(*No SG)'."""
    entries = []
    for mat in materials:
        if get_shading_group(mat):
            entries.append(mat)
        else:
            entries.append(mat + NO_SG_SUFFIX)
    return entries


def find_objects_without_shader():
    all_meshes = cmds.ls(type="mesh", long=True)
    objects_without_shader = []
    for mesh in all_meshes:
        shading_engines = cmds.listConnections(mesh, type="shadingEngine")
        if not shading_engines:
            parents = cmds.listRelatives(mesh, parent=True, fullPath=True)
            if parents:
                objects_without_shader.append(parents[0])
    return objects_without_shader


# ---------------------------------------------------------------------------
# List population
# ---------------------------------------------------------------------------

def list_all_shaders(*args):
    cmds.textField(_ui["search_field"], edit=True, text="")

    namespace = cmds.optionMenu(_ui["namespace_menu"], query=True, value=True)
    only_with_sg = cmds.checkBox(_ui["only_with_sg_check"], query=True, value=True)

    # Listing all materials is equivalent to searching with an empty query.
    # Reuse the shared filter so all namespace modes stay consistent.
    matching = find_materials("", namespace, only_with_sg)
    all_shaders_with_status = _build_material_entries(matching)

    cmds.textScrollList(_ui["material_list"], edit=True, removeAll=True)
    if all_shaders_with_status:
        cmds.textScrollList(_ui["material_list"], edit=True, append=all_shaders_with_status)


def update_material_list(*args):
    search_query = cmds.textField(_ui["search_field"], query=True, text=True).strip()
    namespace = cmds.optionMenu(_ui["namespace_menu"], query=True, value=True)
    only_with_sg = cmds.checkBox(_ui["only_with_sg_check"], query=True, value=True)

    matching = find_materials(search_query, namespace, only_with_sg)
    matching_with_status = _build_material_entries(matching)

    cmds.textScrollList(_ui["material_list"], edit=True, removeAll=True)
    if matching_with_status:
        cmds.textScrollList(_ui["material_list"], edit=True, append=matching_with_status)


# ---------------------------------------------------------------------------
# Material operations
# ---------------------------------------------------------------------------

def assign_selected_material(*args):
    selected = cmds.textScrollList(_ui["material_list"], query=True, selectItem=True)
    if not selected:
        _msg("No material selected.", ok=False)
        return

    material_name = _clean_name(selected[0])
    selected_objects = cmds.ls(selection=True)
    if not selected_objects:
        _msg("No objects selected.", ok=False)
        return

    # hyperShade(assign=...) acts on the whole current selection in one call.
    cmds.hyperShade(assign=material_name)
    _msg("Shader {} assigned to {} object(s).".format(material_name, len(selected_objects)))


def _mesh_shape(object_name):
    """Full path of the object's single renderable mesh shape, else None.

    Face sets can only be re-applied component by component when both sides
    resolve to exactly one mesh shape; anything else is handled whole-object.
    """
    if cmds.nodeType(object_name) == "mesh":
        shapes = cmds.ls(object_name, long=True) or []
    else:
        shapes = cmds.listRelatives(object_name, shapes=True,
                                    noIntermediate=True, fullPath=True) or []
        shapes = [shape for shape in shapes if cmds.nodeType(shape) == "mesh"]
    return shapes[0] if len(shapes) == 1 else None


def _mesh_topology(object_name):
    """(faces, vertices, edges) for a poly object, else None.

    polyEvaluate answers with a string ("Nothing counted ...") for non-poly
    objects, so the counts are type-checked before they are ever compared.
    """
    try:
        counts = (cmds.polyEvaluate(object_name, face=True),
                  cmds.polyEvaluate(object_name, vertex=True),
                  cmds.polyEvaluate(object_name, edge=True))
    except Exception:
        return None
    return counts if all(isinstance(count, int) for count in counts) else None


def _face_components(engine, shape_path):
    """Component strings of the engine's per-face membership on one shape.

    Membership is recorded on the shape and cmds.sets may report it by short
    name, so both sides are resolved to full paths rather than testing a
    transform path against a shape name. Ranges are kept exactly as Maya
    reports them ("f[0:99]"): re-prefixing those onto a target costs one
    string per member, while flattening would cost one per face.
    """
    components = []
    for member in cmds.sets(engine, query=True) or []:
        if "." not in member:
            continue  # whole-object membership carries no face information
        node, component = member.split(".", 1)
        resolved = cmds.ls(node, long=True) or []
        if not resolved:
            continue
        # Accept the shape itself, and a transform path for the same shape:
        # which one cmds.sets reports varies with name ambiguity.
        if resolved[0] == shape_path or _mesh_shape(resolved[0]) == shape_path:
            components.append(component)
    return components


def _transfer_face_sets(source_object, target_objects, shading_engines,
                        material_count):
    """Re-apply a per-face source's shading-group membership to its targets.

    A target receives the source's face sets when its face, vertex and edge
    counts all match. Equal counts are strong evidence of a duplicate or a
    shared base mesh, but they do not prove identical face order, so the
    reported message names how many targets took which path. Targets that do
    not match are left untouched instead of being downgraded to one arbitrary
    material, which is the guarantee the single-material path already gives.

    The selection is never changed here: cmds.sets addresses its components
    directly, so nothing in this path reads the current selection.
    """
    source_shape = _mesh_shape(source_object)
    source_topology = _mesh_topology(source_object) if source_shape else None

    face_sets = []
    if source_shape:
        for engine in shading_engines:
            components = _face_components(engine, source_shape)
            if components:
                face_sets.append((engine, components))

    if not source_topology or len(face_sets) < 2:
        # Reachable when the source is not a single poly mesh, or when its
        # extra materials come through history without holding face membership.
        _msg("Source has {} materials but no readable per-face assignment; "
             "transfer cancelled to avoid replacing the targets' shading."
             .format(material_count), ok=False)
        return

    transferred, skipped = [], []
    cmds.undoInfo(openChunk=True, chunkName="Transfer Material")
    try:
        for target in target_objects:
            target_shape = _mesh_shape(target)
            if not target_shape or _mesh_topology(target) != source_topology:
                skipped.append(target)
                continue
            for engine, components in face_sets:
                cmds.sets(["{}.{}".format(target_shape, component)
                           for component in components],
                          edit=True, forceElement=engine)
            transferred.append(target)
    finally:
        # Never leave Maya's undo queue inside an open chunk.
        cmds.undoInfo(closeChunk=True)

    if not transferred:
        _msg("Source has {} materials (per-face); no target matched its face, "
             "vertex and edge counts, so nothing was changed."
             .format(material_count), ok=False)
        return

    summary = ("Source has {} materials (per-face); transferred {} face set(s) "
               "to {} target(s).".format(material_count, len(face_sets),
                                         len(transferred)))
    if skipped:
        _msg(summary + " Skipped {} target(s) with different face, vertex or "
                       "edge counts; their shading is unchanged."
             .format(len(skipped)), ok=False)
    else:
        _msg(summary)


def transfer_material(*args):
    # Full DAG paths provide stable object identification in complex
    # hierarchies (same convention as create_and_assign_material / find_materials).
    selected_objects = cmds.ls(selection=True, long=True) or []
    if len(selected_objects) < 2:
        _msg("Please select at least two objects (source first, then targets).", ok=False)
        return

    source_object = selected_objects[0]
    target_objects = selected_objects[1:]

    shading_engines = _assigned_shading_engines(source_object)
    materials = _materials_from_shading_engines(shading_engines)
    if not materials:
        _msg("No material found for {}.".format(source_object), ok=False)
        return

    if len(materials) > 1:
        # Per-face source: reproduce its face sets instead of picking one
        # material for the whole target.
        _transfer_face_sets(source_object, target_objects, shading_engines,
                            len(materials))
        return

    material = materials[0]
    cmds.undoInfo(openChunk=True, chunkName="Transfer Material")
    try:
        # One select + one hyperShade call over all targets: faster than a
        # per-object loop. The undo chunk groups selection, assignment, and
        # selection restoration into one undoable operation.
        cmds.select(target_objects, replace=True)
        cmds.hyperShade(assign=material)
    finally:
        try:
            # Restore the source+targets selection the user started with; the
            # old per-object select loop left only the last target selected.
            cmds.select(selected_objects, replace=True)
        finally:
            # Never leave Maya's undo queue inside an open chunk.
            cmds.undoInfo(closeChunk=True)

    _msg("Material {} assigned to {} target(s).".format(material, len(target_objects)))


# Shading-group slots duplicated as part of a full material copy:
_SG_SHADER_SLOTS = ("surfaceShader", "displacementShader", "volumeShader")


def duplicate_shading_network(*args):
    """Duplicate the selected material as a fully independent copy.

    Creates a new shading group and, for every connected slot (surface,
    displacement, volume), duplicates that shader's upstream network with clean,
    unique names - Maya's "pasted__" prefix and trailing numbers stripped - then
    reconnects each to the new shading group. Displacement and volume shaders are
    preserved. Works from the Matching Materials list, or from a material /
    shading group selected in the scene or Node Editor.
    """
    engines = _resolve_shading_engines()
    if not engines:
        _msg("Select a material in the list, or a material / shading group in the scene / Node Editor.", ok=False)
        return

    made, copied = [], []
    for sg in engines:
        if not cmds.objExists(sg):
            continue
        new_sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                           name=_make_unique_name(_clean_shader_name(sg)))
        surface_new = None
        for slot in _SG_SHADER_SLOTS:
            src_plugs = cmds.listConnections(sg + "." + slot, source=True,
                                             destination=False, plugs=True) or []
            if not src_plugs:
                continue
            src_node, src_attr = src_plugs[0].split(".", 1)
            dup_nodes = cmds.duplicate(src_node, upstreamNodes=True, returnRootsOnly=False) or []
            if not dup_nodes:
                continue
            root, root_new = dup_nodes[0], dup_nodes[0]
            for old_node in dup_nodes:
                if not cmds.objExists(old_node):
                    continue
                try:
                    renamed = cmds.rename(old_node, _make_unique_name(_clean_shader_name(old_node)))
                except Exception:
                    renamed = old_node
                copied.append(renamed)
                if old_node == root:
                    root_new = renamed
            try:
                cmds.connectAttr(root_new + "." + src_attr, new_sg + "." + slot, force=True)
            except Exception:
                pass
            if slot == "surfaceShader":
                surface_new = root_new

        # give the new shading group a clean name based on its surface shader
        if surface_new:
            try:
                new_sg = cmds.rename(new_sg, _make_unique_name(surface_new + "SG"))
            except Exception:
                pass
        made.append(new_sg)

    if not made:
        _msg("Duplicate failed.", ok=False)
        return

    cmds.select(made, replace=True)
    update_material_list()
    new_materials = cmds.ls(copied, materials=True)
    label = new_materials[0] if new_materials else made[0]
    _msg("Duplicated material (surface + displacement / volume preserved): {}".format(label))


def delete_material(*args):
    selected = cmds.textScrollList(_ui["material_list"], query=True, selectItem=True)
    if selected:
        material_name = _clean_name(selected[0])
    else:
        scene_sel = cmds.ls(selection=True, materials=True)
        if not scene_sel:
            _msg("No material selected.", ok=False)
            return
        material_name = scene_sel[0]

    shading_group = get_shading_group(material_name)
    if shading_group:
        cmds.delete(shading_group)
    cmds.delete(material_name)
    update_material_list()
    _msg("Material {} and its shading group deleted.".format(material_name))


def _available_material_types():
    """Return the material node types that are registered, in a friendly order
    (renderer materials only appear when their plugin is loaded)."""
    candidates = ["lambert", "blinn", "phong", "standardSurface",
                  "aiStandardSurface", "VRayMtl", "RedshiftMaterial"]
    registered = set(cmds.allNodeTypes())
    return [t for t in candidates if t in registered]


def create_and_assign_material(*args):
    """Create a material of the type chosen in the dropdown and assign it to the
    current selection (if anything is selected)."""
    mat_type = cmds.optionMenu(_ui["new_material_type"], query=True, value=True)
    if not mat_type:
        _msg("Choose a material type first.", ok=False)
        return

    selection = cmds.ls(selection=True, long=True) or []
    shader = cmds.shadingNode(mat_type, asShader=True)
    shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
    cmds.connectAttr(shader + ".outColor", shading_group + ".surfaceShader", force=True)

    if selection:
        cmds.sets(selection, edit=True, forceElement=shading_group)
        _msg("Created {} and assigned to {} object(s).".format(shader, len(selection)))
    else:
        cmds.select(shader, replace=True)
        _msg("Created {} (no objects selected to assign).".format(shader))
    update_material_list()


# ---------------------------------------------------------------------------
# Texture consolidation (merge duplicate file nodes)
# ---------------------------------------------------------------------------

def _collect_file_nodes_from_selection():
    """Return file texture nodes from the current selection.

    Includes any selected file nodes plus every file node upstream of selected
    shaders / shading engines and of the Matching Materials list selection.
    """
    seeds = []
    list_ctrl = _ui.get("material_list")
    if list_ctrl and cmds.textScrollList(list_ctrl, exists=True):
        selected = cmds.textScrollList(list_ctrl, query=True, selectItem=True)
        if selected:
            name = _clean_name(selected[0])
            if cmds.objExists(name):
                seeds.append(name)
    seeds += (cmds.ls(selection=True) or [])

    files = set()
    for node in seeds:
        if not cmds.objExists(node):
            continue
        if cmds.nodeType(node) == "file":
            files.add(node)
            continue
        if cmds.nodeType(node) == "shadingEngine":
            shaders = cmds.listConnections(node + ".surfaceShader", source=True, destination=False) or []
            node = shaders[0] if shaders else node
        history = cmds.listHistory(node) or []
        for f in (cmds.ls(history, type="file") or []):
            files.add(f)
    return list(files)


def _file_texture_key(file_node):
    """Identity used to decide two file nodes are 'the same' texture."""
    path = cmds.getAttr(file_node + ".fileTextureName") or ""
    try:
        color_space = cmds.getAttr(file_node + ".colorSpace")
    except Exception:
        color_space = ""
    return (path.replace("\\", "/").lower(), color_space)


def _rewire_file_outputs(source_file, target_file):
    """Move every outgoing connection from source_file onto target_file."""
    conns = cmds.listConnections(source_file, source=False, destination=True,
                                 plugs=True, connections=True) or []
    # connections=True returns flat pairs: [source_file.attr, destNode.attr, ...]
    for i in range(0, len(conns), 2):
        src_plug, dest_plug = conns[i], conns[i + 1]
        attr = src_plug.split(".", 1)[1]
        new_src = target_file + "." + attr
        try:
            cmds.disconnectAttr(src_plug, dest_plug)
            if not cmds.isConnected(new_src, dest_plug):
                cmds.connectAttr(new_src, dest_plug, force=True)
        except Exception as exc:
            cmds.warning("Could not rewire {} -> {}: {}".format(src_plug, dest_plug, exc))


def merge_duplicate_file_nodes(*args):
    """Merge duplicate file-texture nodes among the selected shaders.

    File nodes that resolve to the same texture (same path + color space) are
    consolidated onto one shared file node: every downstream connection is
    rewired to it and the redundant copies (plus their orphaned place2dTexture
    nodes) are deleted. Works across different shaders. Select shaders (list or
    scene / Node Editor) or file nodes first.
    """
    file_nodes = _collect_file_nodes_from_selection()
    if len(file_nodes) < 2:
        _msg("Select shaders (or file nodes) that share duplicate textures.", ok=False)
        return

    groups = {}
    for f in file_nodes:
        groups.setdefault(_file_texture_key(f), []).append(f)

    merged = 0
    orphan_place2d = []
    for nodes in groups.values():
        if len(nodes) < 2:
            continue
        master = sorted(nodes)[0]
        for dup in nodes:
            if dup == master or cmds.referenceQuery(dup, isNodeReferenced=True):
                continue
            _rewire_file_outputs(dup, master)
            orphan_place2d += cmds.listConnections(dup, type="place2dTexture") or []
            cmds.delete(dup)
            merged += 1

    for place2d in set(orphan_place2d):
        if cmds.objExists(place2d) and not (cmds.listConnections(place2d, source=False, destination=True) or []):
            cmds.delete(place2d)

    if merged:
        _msg("Merged {} duplicate file node(s).".format(merged))
    else:
        _msg("No duplicate file textures found in the selection.", ok=False)


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def select_material_of_selected_item(*args):
    selected = cmds.textScrollList(_ui["material_list"], query=True, selectItem=True)
    if selected:
        material_name = _clean_name(selected[0])
        cmds.select(material_name, replace=True)
        cmds.AttributeEditor()


def select_material_of_selected_object(*args):
    selected_shapes = _selected_shapes()
    if not selected_shapes:
        _msg("No objects selected.", ok=False)
        return

    shading_engines = []
    for shape in selected_shapes:
        # listConnections(shape) can include initialShadingGroup after all of
        # its members have been reassigned. listSets reports only the shading
        # groups that currently contain the object or one of its components.
        shading_engines += cmds.listSets(object=shape, type=1) or []
    shading_engines = _unique(shading_engines)
    if not shading_engines:
        _msg("Selected object has no shading engine.", ok=False)
        return

    materials = _materials_from_shading_engines(shading_engines)
    if not materials:
        _msg("No materials connected to the selected object.", ok=False)
        return

    cmds.textScrollList(_ui["material_list"], edit=True, removeAll=True)
    cmds.textScrollList(_ui["material_list"], edit=True, append=materials)
    cmds.textScrollList(_ui["material_list"], edit=True, selectItem=materials[0])
    if len(materials) == 1:
        _msg("Material {} listed in Matching Materials.".format(materials[0]))
    else:
        _msg(
            "{} materials listed in Matching Materials. "
            "Select one to choose its assigned objects or faces.".format(len(materials))
        )


def select_objects_with_selected_material(*args):
    selected = cmds.textScrollList(_ui["material_list"], query=True, selectItem=True)
    if selected:
        material_name = _clean_name(selected[0])
    else:
        scene_sel = cmds.ls(selection=True, materials=True)
        if not scene_sel:
            _msg("No material selected.", ok=False)
            return
        material_name = scene_sel[0]

    shading_groups = get_shading_groups(material_name)
    if not shading_groups:
        _msg("No shading group found for {}.".format(material_name), ok=False)
        return

    members = []
    for shading_group in shading_groups:
        members += cmds.sets(shading_group, query=True) or []
    members = _unique(members)
    if members:
        cmds.select(members, replace=True)
        _msg(
            "Selected {} object(s) / component assignment(s) using {}.".format(
                len(members), material_name
            )
        )
    else:
        _msg("No objects found with {} applied.".format(material_name), ok=False)


def select_geometry_from_list(*args):
    selected_geometry = cmds.textScrollList(_ui["geometry_list"], query=True, selectItem=True)
    if selected_geometry:
        cmds.select(selected_geometry)


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------

def _resolve_material_for_export():
    """Return the material selected for export.

    The Matching Materials list remains the preferred source.  When the list
    has no selection, accept a material selected in Maya or resolve a selected
    shading engine from its surfaceShader connection.  This also covers
    shading-engine selections made in the Node Editor.
    """
    list_ctrl = _ui.get("material_list")
    if list_ctrl and cmds.textScrollList(list_ctrl, exists=True):
        selected = cmds.textScrollList(list_ctrl, query=True, selectItem=True)
        if selected:
            return _clean_name(selected[0])

    for node in (cmds.ls(selection=True) or []):
        if not cmds.objExists(node):
            continue

        if cmds.nodeType(node) == "shadingEngine":
            connected = cmds.listConnections(
                node + ".surfaceShader",
                source=True,
                destination=False,
            ) or []
            materials = cmds.ls(connected, materials=True) or []
            if materials:
                return materials[0]
        elif cmds.ls(node, materials=True):
            return node

    return None


def export_shader(*args):
    material = _resolve_material_for_export()
    if not material:
        _msg(
            "No material selected. Select a material from the list, or select "
            "a material / shading group in the scene / Node Editor.",
            ok=False,
        )
        return

    if not cmds.objExists(material):
        _msg("Material '{}' not found.".format(material), ok=False)
        return

    # Select the shader, its full upstream network, and its shading group.
    network = set(cmds.listHistory(material) or [])
    network.add(material)
    shading_group = get_shading_group(material)
    if shading_group:
        network.add(shading_group)
    cmds.select(list(network), replace=True, noExpand=True)

    project_dir = cmds.workspace(query=True, rootDirectory=True)
    default_path = project_dir + "renderData/shaders/"
    file_path = cmds.fileDialog2(
        fileMode=0, caption="Export Shader",
        startingDirectory=default_path, fileFilter="Maya ASCII (*.ma)",
    )
    if not file_path:
        return

    cmds.file(file_path[0], force=True, options="v=0;", type="mayaAscii", exportSelected=True)
    _msg("Shader network for {} exported to {}.".format(material, file_path[0]))


def import_shader(*args):
    assign_to_selected = cmds.checkBox(_ui["assign_checkbox"], query=True, value=True)
    selected_objects = cmds.ls(selection=True) if assign_to_selected else []

    project_dir = cmds.workspace(query=True, rootDirectory=True)
    default_path = project_dir + "renderData/shaders/"
    file_path = cmds.fileDialog2(
        fileMode=1, caption="Import Shader",
        startingDirectory=default_path, fileFilter="Maya ASCII (*.ma)",
    )
    if not file_path:
        return

    existing_materials = set(cmds.ls(materials=True, long=True))
    cmds.file(file_path[0], i=True, type="mayaAscii", options="v=0;")
    new_materials = set(cmds.ls(materials=True, long=True)) - existing_materials

    if assign_to_selected:
        if not selected_objects:
            _msg("No objects selected for assignment.", ok=False)
            return
        if not new_materials:
            _msg("No new materials were found in the imported file.", ok=False)
            return
        material = list(new_materials)[0]
        assign_material_to_objects(material, selected_objects)
        cmds.select(selected_objects)
        _msg("Imported and assigned {} to {} object(s).".format(material, len(selected_objects)))
    else:
        _msg("Imported {} new material(s).".format(len(new_materials)))


def assign_material_to_objects(material, objects):
    """Assign ``material`` to each object in ``objects``."""
    if not objects:
        cmds.warning("No objects provided for material assignment.")
        return
    for obj in objects:
        try:
            cmds.select(obj)
            cmds.hyperShade(assign=material)
        except Exception as exc:
            cmds.warning("Failed to assign material to {}: {}".format(obj, exc))


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def rename_shading_groups(*args):
    shading_groups = cmds.ls(type="shadingEngine")
    default_shading_groups = ["initialShadingGroup", "initialParticleSE"]
    renamed_count = 0

    for sg in shading_groups:
        if sg in default_shading_groups:
            continue
        if cmds.referenceQuery(sg, isNodeReferenced=True):
            continue
        connected_materials = cmds.listConnections(sg + ".surfaceShader")
        if connected_materials:
            material_name = connected_materials[0]
            new_sg_name = "{}SG".format(material_name)
            if not cmds.objExists(new_sg_name):
                cmds.rename(sg, new_sg_name)
                renamed_count += 1

    _msg("Successfully renamed {} shading group(s).".format(renamed_count))


def delete_unused_nodes(*args):
    mel.eval('hyperShadePanelMenuCommand("hyperShadePanel1", "deleteUnusedNodes")')
    _msg("Unused nodes deleted.")


def search_default_lambert(*args):
    geometry_objs = cmds.ls(type="mesh", long=True)
    default_geometry_objs = []

    for obj in geometry_objs:
        connections = cmds.listConnections(obj + ".instObjGroups", type="shadingEngine")
        if connections and "initialShadingGroup" in connections[0]:
            parents = cmds.listRelatives(obj, parent=True, fullPath=True)
            if parents:
                default_geometry_objs.append(cmds.ls(parents[0], long=False)[0])

    cmds.textScrollList(_ui["geometry_list"], edit=True, removeAll=True)
    if default_geometry_objs:
        cmds.textScrollList(_ui["geometry_list"], edit=True, append=default_geometry_objs)
        _msg("Found {} object(s) with the default Lambert shader.".format(len(default_geometry_objs)))
    else:
        _msg("No geometry found in the default shading group.", ok=False)


def search_no_shader_assigned(*args):
    objects_without_shader = find_objects_without_shader()
    cmds.textScrollList(_ui["geometry_list"], edit=True, removeAll=True)
    if objects_without_shader:
        cmds.textScrollList(_ui["geometry_list"], edit=True, append=objects_without_shader)
        _msg("Found {} object(s) without shader assignment.".format(len(objects_without_shader)))
    else:
        _msg("No objects found without shader assignment.", ok=False)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _saved_collapse(key, default):
    """Return the remembered collapse state for a section, or the default."""
    var = _OPTVAR_PREFIX + key
    if cmds.optionVar(exists=var):
        return bool(cmds.optionVar(query=var))
    return default


def _save_collapse(key, collapsed):
    cmds.optionVar(intValue=(_OPTVAR_PREFIX + key, int(collapsed)))


def _style_field(control):
    """Best-effort Qt styling for an editable field / dropdown:

    forces dark text on the bright amber background, and recolors the dropdown
    popup's selection from Qt's system blue to the palette amber. Safe no-op if
    PySide / shiboken is unavailable.
    """
    try:
        from maya.OpenMayaUI import MQtUtil
        pointer = MQtUtil.findControl(control)
        if not pointer:
            return
        try:
            from shiboken2 import wrapInstance
            from PySide2 import QtWidgets
        except ImportError:
            from shiboken6 import wrapInstance
            from PySide6 import QtWidgets
        widget = wrapInstance(int(pointer), QtWidgets.QWidget)
        widget.setStyleSheet(
            "QComboBox, QLineEdit {"
            " color: rgb(34, 24, 8); background-color: rgb(204, 135, 56); }"
            "QListView, QAbstractItemView {"
            " background: rgb(52, 48, 42); color: rgb(216, 210, 200);"
            " selection-background-color: rgb(198, 126, 42);"
            " selection-color: rgb(34, 24, 8); }"
            "QMenu { background: rgb(52, 48, 42); color: rgb(216, 210, 200); }"
            "QMenu::item:selected { background: rgb(198, 126, 42); color: rgb(34, 24, 8); }")
    except Exception:
        pass


def show():
    """Build and show the Materialist window.

    Collapsible, scrollable sections; each section's collapse state and the
    window geometry are remembered between sessions.
    """
    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME, window=True)

    window = cmds.window(WINDOW_NAME, title=WINDOW_TITLE, sizeable=True,
                         backgroundColor=COLOR_BG, widthHeight=(340, 760))
    cmds.scrollLayout(childResizable=True, backgroundColor=COLOR_BG)
    root = cmds.columnLayout(adjustableColumn=True, rowSpacing=6, backgroundColor=COLOR_BG)

    def _section(label, key, collapse_default=False):
        cmds.setParent(root)
        cmds.frameLayout(label=label, collapsable=True,
                         collapse=_saved_collapse(key, collapse_default),
                         collapseCommand=lambda k=key: _save_collapse(k, True),
                         expandCommand=lambda k=key: _save_collapse(k, False),
                         marginWidth=4, marginHeight=6, backgroundColor=COLOR_BG)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=6, backgroundColor=COLOR_BG)

    # --- Browse -----------------------------------------------------------
    _section("Browse", "browse")
    cmds.button(label="List All Materials", backgroundColor=BTN_PRIMARY,
                command=lambda *_: _repeatable(list_all_shaders),
                annotation="List all materials in the scene; narrow down with the Has SG checkbox")
    cmds.text(label="Search Materials:", align="left", font="boldLabelFont", height=20)
    _ui["search_field"] = cmds.textField(backgroundColor=COLOR_FIELD,
                                          placeholderText="Start typing material name and press enter",
                                          changeCommand=update_material_list,
                                          annotation="Type to search materials")
    _style_field(_ui["search_field"])
    _ui["only_with_sg_check"] = cmds.checkBox(label="Has SG (filter materials with a shading group)",
                                              value=True, backgroundColor=COLOR_BG,
                                              onCommand=update_material_list, offCommand=update_material_list,
                                              annotation="Filter to materials that have a shading group")
    cmds.text(label="Limit by Namespace:", align="left", font="boldLabelFont", height=20)
    _ui["namespace_menu"] = cmds.optionMenu(backgroundColor=COLOR_FIELD,
                                            changeCommand=update_material_list,
                                            annotation="Select namespace to filter materials")
    cmds.menuItem(label="All Namespaces")
    cmds.menuItem(label="Has no Namespace")
    for namespace in (cmds.namespaceInfo(listOnlyNamespaces=True) or []):
        if namespace not in ["UI", "shared"]:
            cmds.menuItem(label=namespace)
    _style_field(_ui["namespace_menu"])
    cmds.text(label="Matching Materials:", align="left", font="boldLabelFont", height=20)
    _ui["material_list"] = cmds.textScrollList(height=140, allowMultiSelection=False,
                                               backgroundColor=COLOR_PANEL,
                                               doubleClickCommand=select_material_of_selected_item,
                                               annotation="Select with double click")

    # --- Assign & Select --------------------------------------------------
    _section("Assign & Select", "assign")
    cmds.button(label="Assign to Selection", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(assign_selected_material),
                annotation="Assign selected material to selected objects")
    cmds.button(label="Select Material of Selected Object", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(select_material_of_selected_object),
                annotation="List all materials assigned to the selected object or its faces")
    cmds.button(label="Select Objects with Selected Material", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(select_objects_with_selected_material),
                annotation="Select whole objects or exact faces assigned to the selected material")
    cmds.button(label="Transfer Material", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(transfer_material),
                annotation="Select source object first, then targets; targets inherit the same shader")

    _section("Edit", "edit")
    cmds.text(label="Create Material:", align="left", font="boldLabelFont", height=20)
    _ui["new_material_type"] = cmds.optionMenu(backgroundColor=COLOR_FIELD,
                                               annotation="Material type to create")
    for _ntype in _available_material_types():
        cmds.menuItem(label=_ntype)
    _style_field(_ui["new_material_type"])
    cmds.button(label="Create and Assign", backgroundColor=BTN_PRIMARY,
                command=lambda *_: _repeatable(create_and_assign_material),
                annotation="Create the chosen material type and assign it to the selected objects")
    cmds.separator(height=6, style="none")
    cmds.button(label="Duplicate Shading Network", backgroundColor=BTN_MATERIAL,
                command=lambda *_: _repeatable(duplicate_shading_network),
                annotation="Duplicate the selected material into a new shading group with clean names "
                           "(no prefix/suffix), preserving displacement and volume shaders. "
                           "Works from the list or a material/shading group selected in the scene/Hypershade.")
    cmds.button(label="Delete Material", backgroundColor=BTN_DANGER,
                command=lambda *_: _repeatable(delete_material),
                annotation="Delete the selected shading network (destructive)")

    _section("Cleanup", "cleanup")
    cmds.button(label="Batch Rename Shading Groups", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(rename_shading_groups),
                annotation="Rename shading groups to match their shader name")
    cmds.button(label="Delete Unused Nodes", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(delete_unused_nodes),
                annotation="Delete unused nodes, like in Hypershade")
    cmds.button(label="Merge Duplicate Files", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(merge_duplicate_file_nodes),
                annotation="Among the selected shaders, merge file nodes that point to the same "
                           "texture onto one shared file node and delete the duplicates")

    _section("Export / Import", "export")
    cmds.button(label="Export Shader", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(export_shader), annotation="Export selected shader")
    cmds.button(label="Import Shader", backgroundColor=BTN_ACTION,
                command=lambda *_: _repeatable(import_shader), annotation="Import shader from file")
    _ui["assign_checkbox"] = cmds.checkBox(label="Assign imported material to selected objects",
                                           value=True, backgroundColor=COLOR_BG,
                                           annotation="Assign imported material to selected objects")

    _section("Diagnostics", "diagnostics", collapse_default=True)
    cmds.text(label="Objects With No Assignments:", align="left", font="boldLabelFont", height=20)
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnAlign=(1, "left"),
                   columnWidth=[(1, 150), (2, 150)], backgroundColor=COLOR_BG)
    cmds.button(label="Initial Shading Group", backgroundColor=BTN_SEARCH,
                command=lambda *_: _repeatable(search_default_lambert),
                annotation="Find objects still on the initial shading group")
    cmds.button(label="No Shader Assigned", backgroundColor=BTN_SEARCH,
                command=lambda *_: _repeatable(search_no_shader_assigned),
                annotation="Find objects with no shader assigned")
    cmds.setParent("..")
    _ui["geometry_list"] = cmds.textScrollList(height=140, allowMultiSelection=True,
                                               backgroundColor=COLOR_PANEL,
                                               doubleClickCommand=select_geometry_from_list,
                                               annotation="Select with double click / Shift+double / Ctrl+double")

    cmds.showWindow(window)


if __name__ == "__main__":
    show()
