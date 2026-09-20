# 기기 기록 검증 현황 — 2026-09-12 목차/표지 작업분

`~/prj/reading_manager/DATA_MODEL.md` §0.5·§6.1.1 이 **MacDroid 마운트는 읽기도 쓰기도
신뢰할 수 없다**고 확정했습니다(쓴 내용이 9시간 뒤·MacDroid 종료 후에도 기기에 없던
사례가 MTP 대조로 기록됨). 그 기준으로 이전 작업분을 재평가합니다.

## 현황

| 책 | 기록 경로 | 검증 | 상태 |
|---|---|---|---|
| 그리스 신화: 트로이 전쟁 | libmtp (`tools/mtp_put.c`) | `mtp-getfile` 재다운로드 md5 대조 | ✅ 확정 |
| 성경전서 새번역 | MacDroid 마운트 `cp` | 사용자가 기기에서 목차·하이라이트 확인 | ✅ 확정 (간접) |
| 권력과 진보 | MacDroid 마운트 `cp` | **없음** | ⚠️ **미검증** |

## 미검증 건

『권력과 진보』(목차 18 → 124항목, `49f25859…` 아님 — 해당 파일 md5 는 아래 절차로 확인)는
MacDroid Activities 가 업로드 완료를 표시한 것 외에 증거가 없습니다. §0.5 기준으로는
증거로 치지 않습니다. 이 책은 하이라이트가 없어 **실패하더라도 손실은 목차 개선분뿐**이며,
원본은 `~/Documents/kindle-toc-backup/2026-09-12_power-and-progress/` 에 있습니다.

## 검증 절차

MacDroid 를 **완전히 종료**한 뒤(같은 USB 를 두고 상호 배타적입니다):

```bash
mtp-files | grep -A3 "gweonryeoggwa jinbo"      # 파일 id 확인
mtp-getfile <id> /tmp/verify.kfx
md5 -q /tmp/verify.kfx                          # 로컬 수정본과 대조
```

⚠️ MTP 세션을 한 프로세스에서 두 번 열면 `libusb_claim_interface() = -3` 으로 죽습니다
(`DATA_MODEL.md` §0.5). 검증은 별도 프로세스에서 합니다.

기대값은 `KFX_EDITING.md` 의 작업 기록과 `examples/toc_plans/power-and-progress.json`
으로 재생성해 얻을 수 있습니다 — 기기 원본 백업에 `apply` 를 다시 돌리면 같은 파일이 나옵니다.

## 앞으로

기기 파일 변경은 **MTP 직접 쓰기 + 재다운로드 sha 검증**으로만 합니다.
`KFX_EDITING.md` 의 6단계 중 "복사" 절의 MacDroid 안내는 이 원칙에 맞게 고쳐야 합니다.
