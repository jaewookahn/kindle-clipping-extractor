"""여러 화면이 공유하는 작은 Qt 위젯 헬퍼."""

from PyQt6.QtWidgets import QTableWidgetItem


class SortItem(QTableWidgetItem):
    """표시 텍스트와 실제 정렬 기준값을 분리한 QTableWidgetItem.

    Qt 의 기본 정렬은 표시된 문자열을 비교하므로, 숫자·날짜 컬럼을
    "10"이 "2"보다 앞에 오는 사전식으로 잘못 정렬한다. sort_key 를
    따로 받아 __lt__ 에서 그 값으로 비교한다.
    """

    def __init__(self, text: str, sort_key) -> None:
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other) -> bool:  # noqa: D105 — Qt 정렬 프로토콜
        if isinstance(other, SortItem):
            return self.sort_key < other.sort_key
        return super().__lt__(other)
