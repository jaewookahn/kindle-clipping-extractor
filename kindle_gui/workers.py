"""QThreadPool 워커 헬퍼 — 함수를 백그라운드에서 돌리고 결과를 시그널로 돌려준다.

MacDroid MTP 마운트가 파일 읽기에서 멈출 수 있다는 걸 실측으로 확인했다
(DEVLOG.md 9절 — 정상적인 다른 기기가 동시에 Mass Storage 로 마운트돼
있으면 MTP 읽기가 즉시 거부당한다). 디렉터리 스캔조차 메인 스레드에서 직접
돌리지 않고 항상 여기로 태워서, 그런 상황에서도 앱 전체가 멈추지 않게 한다.
"""

from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal


class WorkerSignals(QObject):
    finished = pyqtSignal(object)   # fn 의 리턴값
    error = pyqtSignal(str)


class Worker(QRunnable):
    """fn(*args, **kwargs) 을 스레드풀에서 실행. 실패해도 UI 스레드는 안 죽는다."""

    def __init__(self, fn: Callable[..., Any], *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            self.signals.error.emit(str(e))
            return
        self.signals.finished.emit(result)


# 실행 중인 워커에 대한 강한 참조.
#
# QThreadPool.start() 는 QRunnable 의 C++ 소유권만 가져간다. 파이썬 쪽에서
# Worker 를 아무도 참조하지 않으면 GC 가 수거해 버리고, 그 순간 Worker 가
# 들고 있던 WorkerSignals(QObject) 도 함께 파괴된다. 그러면 작업이 끝나고
# emit 할 때 "wrapped C/C++ object of type WorkerSignals has been deleted"
# 로 죽고 결과가 영영 전달되지 않는다 — 표지·클리핑이 어떤 책은 뜨고 어떤
# 책은 안 뜨는 경합의 원인이었다. 완료될 때까지 여기에 붙잡아 둔다.
_active_workers: set = set()


def run_in_background(
    fn: Callable[..., Any],
    *args,
    on_done: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    **kwargs,
) -> Worker:
    """QThreadPool.globalInstance() 에 fn 을 던지고 완료/에러 콜백을 연결."""
    worker = Worker(fn, *args, **kwargs)
    _active_workers.add(worker)

    def _release(*_: Any) -> None:
        # 슬롯 실행이 끝난 다음 턴에 참조를 놓는다. 시그널 방출 도중에
        # 마지막 참조를 없애면 Qt 가 아직 그 객체를 쓰고 있을 수 있다.
        QTimer.singleShot(0, lambda: _active_workers.discard(worker))

    if on_done:
        worker.signals.finished.connect(on_done)
    if on_error:
        worker.signals.error.connect(on_error)
    # 사용자 콜백보다 뒤에 연결 — 콜백이 먼저 실행되도록.
    worker.signals.finished.connect(_release)
    worker.signals.error.connect(_release)

    QThreadPool.globalInstance().start(worker)
    return worker
