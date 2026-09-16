#ifndef DEMO_BUFFER_H
#define DEMO_BUFFER_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#define BUF_DEFAULT_CAPACITY 64u
#define BUF_MAX(a, b) ((a) > (b) ? (a) : (b))
#define BUF_ALIGN4(n) (((n) + 3u) & ~3u)
#define BUF_LIMIT_NONE ((size_t)-1)

typedef enum {
    BUF_OK = 0,
    BUF_ERR_FULL = -1,
    BUF_ERR_EMPTY = -2
} buf_status_t;

typedef struct buf_ops {
    int (*write)(void *ctx, const uint8_t *data, size_t len);
    int (*flush)(void *ctx);
} buf_ops_t;

typedef struct buffer {
    uint8_t *data;
    size_t capacity;
    size_t head;
    size_t tail;
    size_t limit;
    buf_ops_t ops;
} buffer_t;

void buffer_init(buffer_t *buf, uint8_t *storage, size_t capacity);
size_t buffer_write(buffer_t *buf, const uint8_t *data, size_t len);
size_t buffer_read(buffer_t *buf, uint8_t *out, size_t len);
bool buffer_is_empty(const buffer_t *buf);
size_t buffer_pending(const buffer_t *buf);
void buffer_set_ops(buffer_t *buf, const buf_ops_t *ops);

#endif
