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


class TransferMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.materialist = _load_materialist()

    def setUp(self):
        self.cmds = mock.MagicMock()
        self.materialist.cmds = self.cmds
        self.materialist._msg = mock.MagicMock()
        self.selection = ["|source", "|targetA", "|targetB"]
        self.materials = ["MaterialA"]

        def ls(*_args, **kwargs):
            if kwargs.get("selection"):
                return list(self.selection)
            if kwargs.get("type") == "shadingEngine":
                return ["MaterialASG"]
            if kwargs.get("materials"):
                return list(self.materials)
            return []

        self.cmds.ls.side_effect = ls
        self.cmds.listHistory.return_value = ["sourceHistory"]
        self.cmds.listConnections.return_value = ["MaterialA"]

    def test_assigns_all_targets_and_restores_selection_in_one_undo_chunk(self):
        self.materials = ["MaterialA", "MaterialA"]

        self.materialist.transfer_material()

        self.cmds.select.assert_has_calls(
            [
                mock.call(["|targetA", "|targetB"], replace=True),
                mock.call(self.selection, replace=True),
            ]
        )
        self.cmds.hyperShade.assert_called_once_with(assign="MaterialA")
        self.cmds.undoInfo.assert_has_calls(
            [
                mock.call(openChunk=True, chunkName="Transfer Material"),
                mock.call(closeChunk=True),
            ]
        )
        self.materialist._msg.assert_called_once_with(
            "Material MaterialA assigned to 2 target(s)."
        )

    def test_multi_material_source_is_rejected_before_targets_change(self):
        self.materials = ["MaterialA", "MaterialB", "MaterialA"]

        self.materialist.transfer_material()

        self.cmds.select.assert_not_called()
        self.cmds.hyperShade.assert_not_called()
        self.cmds.undoInfo.assert_not_called()
        message, = self.materialist._msg.call_args.args
        self.assertIn("transfer cancelled", message)
        self.assertEqual(self.materialist._msg.call_args.kwargs, {"ok": False})

    def test_assignment_failure_restores_selection_and_closes_undo_chunk(self):
        self.cmds.hyperShade.side_effect = RuntimeError("assignment failed")

        with self.assertRaisesRegex(RuntimeError, "assignment failed"):
            self.materialist.transfer_material()

        self.cmds.select.assert_has_calls(
            [
                mock.call(["|targetA", "|targetB"], replace=True),
                mock.call(self.selection, replace=True),
            ]
        )
        self.cmds.undoInfo.assert_has_calls(
            [
                mock.call(openChunk=True, chunkName="Transfer Material"),
                mock.call(closeChunk=True),
            ]
        )


if __name__ == "__main__":
    unittest.main()
