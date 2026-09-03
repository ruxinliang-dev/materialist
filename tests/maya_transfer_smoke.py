"""Run explicitly with mayapy to verify per-face material transfer.

Covers what the mocked unit tests cannot: the real names cmds.sets reports for
shading-group membership, the actual face sets a transfer produces, and that
the whole transfer collapses into one undo step.
"""

try:                                    # already inside a running Maya
    import maya.cmds as cmds
    cmds.ls(selection=True)         # only answers once Maya is running
    STANDALONE = False
except Exception:                       # launched through mayapy
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    STANDALONE = True

import materialist


MESSAGES = []
materialist._msg = lambda text, ok=True: MESSAGES.append((ok, text))


def _make_material(name):
    material = cmds.shadingNode("lambert", asShader=True, name=name)
    shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                              name=name + "SG")
    cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader",
                     force=True)
    return material, shading_group


def _face_map(obj):
    """{face index: shading group} for every face of obj."""
    shape = materialist._mesh_shape(obj)
    faces = cmds.ls(shape + ".f[*]", flatten=True) or []
    mapping = {}
    for shading_group in cmds.listSets(object=shape, type=1) or []:
        members = cmds.ls(cmds.sets(shading_group, query=True) or [],
                          flatten=True) or []
        own = [m for m in members if m.split(".")[0] in
               (shape, shape.split("|")[-1], obj, obj.split("|")[-1])]
        if not own:
            continue
        if any("." not in m for m in own):          # whole-object membership
            for index in range(len(faces)):
                mapping[index] = shading_group
        for member in own:
            if ".f[" in member:
                mapping[int(member.rsplit("[", 1)[1].rstrip("]"))] = shading_group
    return mapping


FAILURES = []


def report():
    """Print the verdict. A failed check must never read as a pass."""
    if FAILURES:
        print("\nFAILED (%d): %s" % (len(FAILURES), ", ".join(FAILURES)))
    else:
        print("\nMaya per-face transfer smoke test passed.")


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (" | " + str(detail) if detail else ""))
    if not condition:
        FAILURES.append(label)


try:
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True, infinity=True)

    source = cmds.polyCube(name="SmokeSource")[0]
    material_a, sg_a = _make_material("SmokeA")
    material_b, sg_b = _make_material("SmokeB")
    material_c, sg_c = _make_material("SmokeC")
    cmds.sets(source + ".f[0:2]", edit=True, forceElement=sg_a)
    cmds.sets(source + ".f[3:5]", edit=True, forceElement=sg_b)

    print("cmds.sets(%s, q=True) -> %r" % (sg_a, cmds.sets(sg_a, query=True)))
    print("selection long name    -> %r" % cmds.ls(source, long=True))

    dupe = cmds.duplicate(source, name="SmokeDupe")[0]
    cmds.sets(dupe, edit=True, forceElement=sg_c)
    coarse = cmds.polyCube(name="SmokeCoarse", subdivisionsX=2, subdivisionsY=2,
                           subdivisionsZ=2)[0]
    cmds.sets(coarse, edit=True, forceElement=sg_c)

    source_map = _face_map(source)
    check("source has two face sets", len(set(source_map.values())) == 2, source_map)

    # 1. matching target receives the source's face sets
    del MESSAGES[:]
    cmds.select([source, dupe], replace=True)
    materialist.transfer_material()
    check("dupe face sets equal source", _face_map(dupe) == source_map, _face_map(dupe))
    check("message reports the transfer",
          "transferred 2 face set(s) to 1 target(s)" in MESSAGES[-1][1], MESSAGES)
    # The source carries two materials. A shading group left in the history
    # graph after a per-face reassignment must not inflate that count.
    check("material count excludes stale shading groups",
          MESSAGES[-1][1].startswith("Source has 2 materials"), MESSAGES[-1][1])

    # 2. one undo restores the whole transfer
    cmds.undo()
    after_undo = set(_face_map(dupe).values())
    check("single undo restores the dupe", after_undo == {sg_c}, after_undo)
    cmds.redo()
    check("redo re-applies the transfer", _face_map(dupe) == source_map)

    # 3. mismatched topology is skipped, not downgraded
    del MESSAGES[:]
    before = _face_map(coarse)
    cmds.select([source, coarse], replace=True)
    materialist.transfer_material()
    check("coarse target untouched", _face_map(coarse) == before, _face_map(coarse))
    check("no-match reported as a warning", MESSAGES[-1][0] is False, MESSAGES)

    # 4. mixed selection reports both paths
    del MESSAGES[:]
    cmds.sets(dupe, edit=True, forceElement=sg_c)
    cmds.select([source, dupe, coarse], replace=True)
    materialist.transfer_material()
    check("mixed: dupe transferred", _face_map(dupe) == source_map)
    check("mixed: coarse untouched", _face_map(coarse) == before)
    check("mixed message names both paths",
          "to 1 target(s)" in MESSAGES[-1][1] and "Skipped 1 target(s)" in MESSAGES[-1][1],
          MESSAGES)

    # 5. single-material source: unchanged behaviour (regression check).
    # hyperShade is a UI command and is unavailable in batch, so this step runs
    # only from a Maya session (script editor: execfile this file).
    if cmds.about(query=True, batch=True):
        print("SKIP  single-material regression | hyperShade needs a Maya session")
        report()
        raise SystemExit(1 if FAILURES else 0)

    del MESSAGES[:]
    plain = cmds.polyCube(name="SmokePlain")[0]
    cmds.sets(plain, edit=True, forceElement=sg_a)
    target = cmds.polyCube(name="SmokePlainTarget")[0]
    cmds.sets(target, edit=True, forceElement=sg_c)
    cmds.select([plain, target], replace=True)
    materialist.transfer_material()
    check("single-material target reassigned", set(_face_map(target).values()) == {sg_a},
          _face_map(target))
    check("selection restored", cmds.ls(selection=True, long=True) ==
          cmds.ls([plain, target], long=True), cmds.ls(selection=True, long=True))
    check("single-material message unchanged",
          MESSAGES[-1][1].startswith("Material ") and MESSAGES[-1][0] is True, MESSAGES)

    report()
finally:
    if STANDALONE:
        maya.standalone.uninitialize()
