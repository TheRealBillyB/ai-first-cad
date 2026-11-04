"""Reading the part list does not check a solid again.

The live page reads the state after every event, under the document lock. A
validity check on a toothed gear takes seconds.
"""

from aicad import Document, kernel

BOX = [{"op": "add", "shape": "box", "size": [10, 20, 30]}]
TALLER = [{"op": "add", "shape": "box", "size": [10, 20, 40]}]


def _count_measures(monkeypatch):
    calls = []
    real = kernel.measure

    def counting(shape, material=None):
        calls.append(shape)
        return real(shape, material)

    monkeypatch.setattr(kernel, "measure", counting)
    return calls


def test_a_second_read_does_not_measure_again(tmp_path, monkeypatch):
    document = Document(tmp_path)
    document.create_part_from_ops("box", BOX)
    calls = _count_measures(monkeypatch)
    first = document.list_parts()
    second = document.list_parts()
    assert first == second
    assert calls == []


def test_a_new_solid_is_measured(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops("box", BOX)
    assert document.list_parts()[0].bounding_box.size.z == 30
    document.update_part_ops("box", TALLER)
    assert document.list_parts()[0].bounding_box.size.z == 40


def test_a_moved_part_reports_where_it_is(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops("box", BOX)
    before = document.list_parts()[0].bounding_box.min.x
    document.place_part("box", position=[100, 0, 0])
    assert document.list_parts()[0].bounding_box.min.x == before + 100
