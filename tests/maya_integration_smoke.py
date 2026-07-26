"""Run explicitly with mayapy to verify component material assignments."""

import maya.standalone


maya.standalone.initialize(name="python")

import maya.cmds as cmds

import materialist


def _make_material(name):
    material = cmds.shadingNode("lambert", asShader=True, name=name)
    shading_group = cmds.sets(
        renderable=True,
        noSurfaceShader=True,
        empty=True,
        name=name + "SG",
    )
    cmds.connectAttr(
        material + ".outColor",
        shading_group + ".surfaceShader",
        force=True,
    )
    return material, shading_group


try:
    cmds.file(new=True, force=True)
    cube, _ = cmds.polyCube(name="MaterialistTestCube")
    material_a, shading_group_a = _make_material("MaterialistTestA")
    material_b, shading_group_b = _make_material("MaterialistTestB")

    cmds.sets(cube + ".f[0:2]", edit=True, forceElement=shading_group_a)
    cmds.sets(cube + ".f[3:5]", edit=True, forceElement=shading_group_b)
    cmds.select(cube, replace=True)

    shapes = materialist._selected_shapes()
    assert len(shapes) == 1, shapes

    shading_groups = cmds.listSets(
        object=shapes[0],
        type=1,
    ) or []
    assert set(shading_groups) == {shading_group_a, shading_group_b}, shading_groups
    materials = materialist._materials_from_shading_engines(shading_groups)
    assert set(materials) == {material_a, material_b}, materials

    assert shading_group_a in materialist.get_shading_groups(material_a)
    assert shading_group_b in materialist.get_shading_groups(material_b)

    members_a = cmds.sets(shading_group_a, query=True) or []
    members_b = cmds.sets(shading_group_b, query=True) or []
    assert any(".f[" in member for member in members_a), members_a
    assert any(".f[" in member for member in members_b), members_b

    print("Maya component-material integration smoke test passed.")
finally:
    maya.standalone.uninitialize()
