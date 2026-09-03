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

    def test_multi_material_source_without_face_membership_is_cancelled(self):
        # No mesh shape resolves, so there is no per-face membership to read.
        # The targets must keep their shading rather than take materials[0].
        self.materials = ["MaterialA", "MaterialB", "MaterialA"]
        self.cmds.listRelatives.return_value = []

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


class PerFaceTransferTests(unittest.TestCase):
    """Per-face sources: the source's face sets, not one arbitrary material."""

    @classmethod
    def setUpClass(cls):
        cls.materialist = _load_materialist()

    def setUp(self):
        self.cmds = mock.MagicMock()
        self.materialist.cmds = self.cmds
        self.materialist._msg = mock.MagicMock()

        # |dupe matches the source's counts; |other does not.
        self.selection = ["|src", "|dupe", "|other"]
        self.materials = ["MaterialA", "MaterialB"]
        self.engines = ["MaterialASG", "MaterialBSG"]
        self.shapes = {
            "|src": "|src|srcShape",
            "|dupe": "|dupe|dupeShape",
            "|other": "|other|otherShape",
        }
        self.topology = {"|src": (6, 8, 12), "|dupe": (6, 8, 12), "|other": (4, 4, 6)}
        # cmds.sets reports membership on the *shape*, by short name. The
        # trailing whole-object member belongs to another object entirely.
        self.members = {
            "MaterialASG": ["srcShape.f[0:2]"],
            "MaterialBSG": ["srcShape.f[3:5]", "otherShape"],
        }
        self.set_edits = []

        def ls(*args, **kwargs):
            if kwargs.get("selection"):
                return list(self.selection)
            if kwargs.get("type") == "shadingEngine":
                return list(self.engines)
            if kwargs.get("materials"):
                return list(self.materials)
            if args and kwargs.get("long"):
                name = args[0]
                if isinstance(name, (list, tuple)):
                    name = name[0] if name else ""
                for full in self.shapes.values():
                    if name == full or full.endswith("|" + name):
                        return [full]
                return [name] if name.startswith("|") else []
            return []

        def node_type(name):
            return "mesh" if name.endswith("Shape") else "transform"

        def list_relatives(name, **kwargs):
            shape = self.shapes.get(name)
            return [shape] if shape else []

        def poly_evaluate(name, **kwargs):
            counts = self.topology.get(name)
            if counts is None:
                return "Nothing counted : no polygonal object is selected."
            faces, verts, edges = counts
            if kwargs.get("face"):
                return faces
            if kwargs.get("vertex"):
                return verts
            return edges

        def sets(*args, **kwargs):
            if kwargs.get("query"):
                return list(self.members.get(args[0], []))
            self.set_edits.append((kwargs.get("forceElement"), list(args[0])))
            return None

        self.cmds.ls.side_effect = ls
        self.cmds.nodeType.side_effect = node_type
        self.cmds.listRelatives.side_effect = list_relatives
        self.cmds.polyEvaluate.side_effect = poly_evaluate
        self.cmds.sets.side_effect = sets
        self.cmds.listHistory.return_value = ["sourceHistory"]
        self.cmds.listConnections.return_value = ["MaterialA", "MaterialB"]

    def test_face_sets_reach_the_matching_target_only(self):
        self.materialist.transfer_material()

        # Short shape names in the set resolve to the source's shape, and the
        # ranges are re-prefixed onto the target's shape unflattened.
        self.assertEqual(
            self.set_edits,
            [
                ("MaterialASG", ["|dupe|dupeShape.f[0:2]"]),
                ("MaterialBSG", ["|dupe|dupeShape.f[3:5]"]),
            ],
        )
        self.cmds.hyperShade.assert_not_called()
        self.cmds.select.assert_not_called()

    def test_membership_reported_as_a_full_path_is_also_matched(self):
        # cmds.sets reports short shape names when they are unambiguous and
        # full paths when they are not; both must resolve to the same shape.
        self.members = {
            "MaterialASG": ["|src|srcShape.f[0:2]"],
            "MaterialBSG": ["|src|srcShape.f[3:5]"],
        }
        self.selection = ["|src", "|dupe"]

        self.materialist.transfer_material()

        self.assertEqual(
            self.set_edits,
            [
                ("MaterialASG", ["|dupe|dupeShape.f[0:2]"]),
                ("MaterialBSG", ["|dupe|dupeShape.f[3:5]"]),
            ],
        )

    def test_mismatched_target_is_reported_not_downgraded(self):
        self.materialist.transfer_material()

        edited_objects = [components[0] for _, components in self.set_edits]
        self.assertFalse(any(obj.startswith("|other") for obj in edited_objects))
        message = self.materialist._msg.call_args.args[0]
        self.assertIn("transferred 2 face set(s) to 1 target(s)", message)
        self.assertIn("Skipped 1 target(s)", message)
        self.assertEqual(self.materialist._msg.call_args.kwargs, {"ok": False})

    def test_all_targets_matching_reports_plain_success(self):
        self.selection = ["|src", "|dupe"]

        self.materialist.transfer_material()

        self.materialist._msg.assert_called_once_with(
            "Source has 2 materials (per-face); transferred 2 face set(s) to "
            "1 target(s)."
        )

    def test_no_matching_target_changes_nothing(self):
        self.selection = ["|src", "|other"]

        self.materialist.transfer_material()

        self.assertEqual(self.set_edits, [])
        message = self.materialist._msg.call_args.args[0]
        self.assertIn("no target matched", message)
        self.assertEqual(self.materialist._msg.call_args.kwargs, {"ok": False})

    def test_single_engine_membership_is_cancelled_not_half_applied(self):
        # MaterialB reaches the source through history but holds no faces on
        # it: transferring only MaterialA's faces would be a partial result.
        self.members["MaterialBSG"] = ["otherShape"]

        self.materialist.transfer_material()

        self.assertEqual(self.set_edits, [])
        self.cmds.undoInfo.assert_not_called()
        message = self.materialist._msg.call_args.args[0]
        self.assertIn("transfer cancelled", message)

    def test_all_face_sets_share_one_undo_chunk(self):
        self.materialist.transfer_material()

        self.assertEqual(
            self.cmds.undoInfo.call_args_list,
            [
                mock.call(openChunk=True, chunkName="Transfer Material"),
                mock.call(closeChunk=True),
            ],
        )

    def test_failure_mid_transfer_still_closes_the_undo_chunk(self):
        def failing_sets(*args, **kwargs):
            if kwargs.get("query"):
                return list(self.members.get(args[0], []))
            raise RuntimeError("assignment failed")

        self.cmds.sets.side_effect = failing_sets

        with self.assertRaisesRegex(RuntimeError, "assignment failed"):
            self.materialist.transfer_material()

        self.cmds.undoInfo.assert_has_calls(
            [
                mock.call(openChunk=True, chunkName="Transfer Material"),
                mock.call(closeChunk=True),
            ]
        )


if __name__ == "__main__":
    unittest.main()
