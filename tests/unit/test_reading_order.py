"""docling's rule-based reading order, ported (operonx_kb.pdf.reading_order)."""

from operonx_kb.pdf.reading_order import reading_order


def test_two_columns_under_a_spanning_title_are_read_column_by_column():
    boxes = [
        (50, 50, 550, 70),  # title across both columns
        (50, 100, 290, 200),  # left column, top
        (310, 100, 550, 180),  # right column, top
        (50, 210, 290, 300),  # left column, bottom
        (310, 190, 550, 300),  # right column, bottom
    ]
    assert reading_order(boxes, 600, 800) == [0, 1, 3, 2, 4]


def test_a_full_width_block_closes_the_columns_above_it():
    boxes = [
        (50, 100, 290, 200),
        (310, 100, 550, 200),
        (50, 220, 550, 260),  # spans: read after both columns
        (50, 280, 290, 350),
        (310, 280, 550, 350),
    ]
    assert reading_order(boxes, 600, 800) == [0, 1, 2, 3, 4]


def test_a_row_of_side_by_side_blocks_reads_left_to_right():
    boxes = [(50, 100, 150, 120), (200, 100, 300, 120), (350, 100, 450, 120)]
    assert reading_order(boxes, 600, 800) == [0, 1, 2]


def test_every_block_comes_out_once():
    boxes = [(10 * i, 5 * (i % 7), 10 * i + 60, 5 * (i % 7) + 4) for i in range(30)]
    assert sorted(reading_order(boxes, 600, 800)) == list(range(30))


def test_same_height_paragraphs_side_by_side_are_columns_not_a_row():
    boxes = [
        (50, 100, 290, 200),  # left column, top
        (310, 100, 550, 200),  # right column, top: as tall as its neighbour
        (50, 210, 290, 300),
        (310, 210, 550, 300),
    ]
    assert reading_order(boxes, 600, 800, row_height=30) == [0, 2, 1, 3]
