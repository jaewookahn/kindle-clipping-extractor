/* mtp_put — 지정한 폴더 id 아래로 파일을 보낸다 (mtp-sendfile은 루트에만 보낸다).
 *   usage: mtp_put <local> <remote-name> <parent-folder-id> [<delete-file-id>]
 * delete-file-id 를 주면 전송 "성공 후"에 지운다 — 실패해도 원본이 남게 하려는 것.
 * MTP에는 덮어쓰기가 없어서 같은 이름이 잠깐 둘 존재하지만, 기기는 잠들어 있고
 * 우리가 곧 하나를 지우므로 문제되지 않는다. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <libmtp.h>

static int progress(uint64_t sent, uint64_t total, void const *d) {
    (void)d;
    static int last = -1;
    int pct = total ? (int)(sent * 100 / total) : 0;
    if (pct != last && pct % 10 == 0) { fprintf(stderr, "  %d%%\n", pct); last = pct; }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s <local> <remote-name> <parent-id> [del-id]\n", argv[0]); return 2; }
    const char *local = argv[1], *name = argv[2];
    uint32_t parent = (uint32_t)strtoul(argv[3], NULL, 10);
    uint32_t delid  = argc > 4 ? (uint32_t)strtoul(argv[4], NULL, 10) : 0;

    struct stat st;
    if (stat(local, &st)) { perror("stat"); return 1; }

    LIBMTP_Init();
    LIBMTP_mtpdevice_t *dev = LIBMTP_Get_First_Device();
    if (!dev) { fprintf(stderr, "기기를 열 수 없다 (MacDroid가 잡고 있지 않은지 확인)\n"); return 1; }

    LIBMTP_devicestorage_t *store = dev->storage;
    if (!store) { fprintf(stderr, "저장소 없음\n"); return 1; }

    LIBMTP_file_t *f = LIBMTP_new_file_t();
    f->filesize = (uint64_t)st.st_size;
    f->filename = strdup(name);
    f->filetype = LIBMTP_FILETYPE_UNKNOWN;
    f->parent_id = parent;
    f->storage_id = store->id;

    fprintf(stderr, "전송: %s → parent=%u, %lld bytes\n", name, parent, (long long)st.st_size);
    int rc = LIBMTP_Send_File_From_File(dev, local, f, progress, NULL);
    if (rc != 0) {
        fprintf(stderr, "전송 실패\n");
        LIBMTP_Dump_Errorstack(dev); LIBMTP_Clear_Errorstack(dev);
        LIBMTP_Release_Device(dev); return 1;
    }
    printf("NEW_FILE_ID=%u\n", f->item_id);
    LIBMTP_destroy_file_t(f);

    if (delid) {
        fprintf(stderr, "구파일 삭제: id=%u\n", delid);
        if (LIBMTP_Delete_Object(dev, delid) != 0) {
            fprintf(stderr, "삭제 실패 — 같은 이름 파일이 둘 남았다. 수동 정리 필요\n");
            LIBMTP_Dump_Errorstack(dev); LIBMTP_Clear_Errorstack(dev);
            LIBMTP_Release_Device(dev); return 1;
        }
        fprintf(stderr, "삭제 완료\n");
    }
    LIBMTP_Release_Device(dev);
    return 0;
}
