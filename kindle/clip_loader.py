"""책 하나의 클리핑을 로드하는 공유 파이프라인.

tui.py(Textual)와 kindle_gui(PyQt6) 양쪽이 같은 함수를 쓴다. UI 프레임워크에
얽매이지 않도록 순수 함수로 뽑아뒀다 — 호출부가 스레드/워커에서 부르고
결과만 받아 화면을 갱신하면 된다.
"""

import contextlib
import io
from typing import List, Tuple

from kindle.ebook import (
    _find_kfx_plugin,
    extract_kfx_info,
    fill_clipping_chapters,
    fill_clipping_kindle_locations,
    fill_clipping_pages,
    fill_clipping_text,
)
from kindle.fileprovider import HINT, is_fileprovider_path, looks_unmaterialized
from kindle.models import Clipping
from kindle.parsers.yjr import parse_yjr


def load_book_clippings(book: dict) -> Tuple[List[Clipping], List[str]]:
    """YJR 파싱 → KFX 텍스트·페이지·챕터·Kindle Location 채우기.

    book: list_kfx_books() 가 돌려주는 dict (+ "title"/"author" 보강된 것).
          "sdr", "kfx", "title" 키를 쓴다.

    Returns:
        (clips, errors) — errors 는 사용자에게 그대로 보여줄 한글 메시지 목록.
        KFX 플러그인이 없거나 추출이 실패해도 예외를 던지지 않고 errors 에 담는다.
    """
    clips: List[Clipping] = []
    errors: List[str] = []

    sdr = book.get("sdr")
    if sdr is None:
        errors.append("SDR 폴더 없음 (책에 어노테이션이 한 번도 저장되지 않음)")
    else:
        yjr_files = list(sdr.glob("*.yjr"))
        if not yjr_files:
            errors.append("SDR 폴더에 YJR 파일 없음 (하이라이트·북마크 없음)")
            # MacDroid File Provider 는 .sdr 내용을 낡은 캐시로 답할 수 있다.
            # 앱에서 고칠 방법이 없어(kindle/fileprovider.py 참조) 안내만 한다.
            if is_fileprovider_path(sdr) and looks_unmaterialized(sdr):
                errors.append(HINT.format(path=sdr))
        for yjr in yjr_files:
            try:
                clips.extend(parse_yjr(yjr, book_title=book["title"]))
            except Exception as e:
                errors.append(f"YJR 파싱 실패 ({yjr.name}): {e}")

    if not clips:
        return clips, errors

    if not _find_kfx_plugin():
        errors.append("Calibre KFX Input 플러그인 없음 — 본문·페이지·챕터 추출 불가 "
                      "(Calibre → 환경설정 → 플러그인 → 'KFX Input' 검색 후 설치)")
        return clips, errors

    # extract_kfx_info 내부의 [warn] 은 print(file=sys.stderr) 라서 호출부가
    # 터미널 alternate screen 이든 GUI 스레드든 화면에 보이지 않고 사라진다.
    # stderr 를 가로채 진짜 원인(예외 메시지)을 에러 목록에 그대로 노출시킨다.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            page_map, kl_offsets, book_text, toc = extract_kfx_info(book["kfx"])
        stderr_text = buf.getvalue().strip()
        if book_text:
            fill_clipping_text(clips, book_text)
        else:
            detail = f" — {stderr_text}" if stderr_text else " (원인 불명)"
            errors.append(f"KFX 본문 추출 실패{detail} "
                          "→ 하이라이트 텍스트가 비어 보일 수 있음")
        if page_map:
            fill_clipping_pages(clips, page_map)
        if toc:
            fill_clipping_chapters(clips, toc)
        if kl_offsets:
            fill_clipping_kindle_locations(clips, kl_offsets)
        else:
            errors.append("KL 맵 없음 → 위치가 raw char offset")
    except Exception as e:
        errors.append(f"KFX 정보 추출 실패: {e}")

    return clips, errors
