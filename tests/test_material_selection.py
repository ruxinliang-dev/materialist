import importlib
import sys
import types
import unittest
from unittest import mock


def _load_materialist():
    maya = types.ModuleType("maya")
    maya_cmds = types.ModuleType("maya.cmds")
    maya_mel = types.ModuleType("maya.mel")
    maya.cmds = maya_cmds
    maya.mel = maya_mel
    sys.modules["maya"] = maya
    sys.modules["maya.cmds"] = maya_cmds
    sys.modules["maya.mel"] = maya_mel
    sys.modules.pop("materialist", None)
    return importlib.import_module("materialist")


class MaterialSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.materialist = _load_materialist()

    def setUp(self):
        self.cmds = mock.MagicMock()
        self.materialist.cmds = self.cmds
        self.materialist._ui = {"material_list": "materialList"}

    def test_get_shading_groups_returns_every_unique_connection(self):
        self.cmds.listConnections.return_value = [
            "Material_ASG",
            "Material_ASG",
            "Material_A_AlternateSG",
        ]

        result = self.materialist.get_shading_groups("Material_A")

        self.assertEqual(result, ["Material_ASG", "Material_A_AlternateSG"])
        self.cmds.listConnections.assert_called_once_with(
            "Material_A.outColor",
            source=False,
            destination=True,
            type="shadingEngine",
        )

    def test_lists_all_materials_assigned_to_selected_mesh(self):
        self.cmds.ls.side_effect = [
            ["|pCube1"],
            ["Material_A"],
            ["Material_B"],
        ]
        self.cmds.listRelatives.return_value = ["|pCube1|pCubeShape1"]
        self.cmds.listSets.return_value = ["Material_ASG", "Material_BSG"]

        def list_connections(node, **kwargs):
            if node == "Material_ASG.surfaceShader":
                return ["Material_A"]
            if node == "Material_BSG.surfaceShader":
                return ["Material_B"]
            return []

        self.cmds.listConnections.side_effect = list_connections

        self.materialist.select_material_of_selected_object()

        self.cmds.textScrollList.assert_has_calls(
            [
                mock.call("materialList", edit=True, removeAll=True),
                mock.call(
                    "materialList",
                    edit=True,
                    append=["Material_A", "Material_B"],
                ),
                mock.call(
                    "materialList",
                    edit=True,
                    selectItem="Material_A",
                ),
            ]
        )
        self.cmds.listSets.assert_called_once_with(
            object="|pCube1|pCubeShape1",
            type=1,
        )

    def test_selects_exact_members_from_all_material_shading_groups(self):
        def text_scroll_list(_control, **kwargs):
            if kwargs.get("query") and kwargs.get("selectItem"):
                return ["Material_A"]
            return None

        self.cmds.textScrollList.side_effect = text_scroll_list
        self.cmds.listConnections.return_value = [
            "Material_ASG",
            "Material_A_AlternateSG",
        ]

        def set_members(shading_group, **_kwargs):
            if shading_group == "Material_ASG":
                return ["|pCube1|pCubeShape1.f[0:3]"]
            return ["|pSphere1"]

        self.cmds.sets.side_effect = set_members

        self.materialist.select_objects_with_selected_material()

        self.cmds.select.assert_called_once_with(
            ["|pCube1|pCubeShape1.f[0:3]", "|pSphere1"],
            replace=True,
        )


if __name__ == "__main__":
    unittest.main()
