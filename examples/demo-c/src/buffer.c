#include "buffer.h"

#include <string.h>

static size_t s_buffer_write_count = 0u;
static size_t s_buffer_read_count = 0u;

static size_t wrap_index(const buffer_t *buf, size_t index)
{
    return index % (buf->capacity ? buf->capacity : 1u);
}

void buffer_init(buffer_t *buf, uint8_t *storage, size_t capacity)
{
    memset(buf, 0, sizeof(*buf));
    buf->data = storage;
    buf->capacity = capacity;
    buf->limit = capacity ? capacity - 1u : 0u;
}

size_t buffer_write(buffer_t *buf, const uint8_t *data, size_t len)
{
    size_t written = 0u;
    size_t i;

    if (!buf || !data || !buf->data) {
        return 0u;
    }
    for (i = 0u; i < len; i++) {
        if (buffer_pending(buf) >= buf->limit) {
            break;
        }
        buf->data[wrap_index(buf, buf->tail)] = data[i];
        buf->tail = wrap_index(buf, buf->tail + 1u);
        written++;
    }
    s_buffer_write_count += written;
    if (written && buf->ops.write) {
        buf->ops.write(buf, data, written);
    }
    return written;
}

size_t buffer_read(buffer_t *buf, uint8_t *out, size_t len)
{
    size_t read = 0u;
    size_t i;

    if (!buf || !out) {
        return 0u;
    }
    for (i = 0u; i < len; i++) {
        if (buffer_is_empty(buf)) {
            break;
        }
        out[i] = buf->data[wrap_index(buf, buf->head)];
        buf->head = wrap_index(buf, buf->head + 1u);
        read++;
    }
    s_buffer_read_count += read;
    return read;
}

bool buffer_is_empty(const buffer_t *buf)
{
    return !buf || buf->head == buf->tail;
}

size_t buffer_pending(const buffer_t *buf)
{
    if (!buf) {
        return 0u;
    }
    return (buf->tail + buf->capacity - buf->head) % (buf->capacity ? buf->capacity : 1u);
}

void buffer_set_ops(buffer_t *buf, const buf_ops_t *ops)
{
    if (buf && ops) {
        buf->ops = *ops;
    }
}

size_t buffer_stats(size_t *writes, size_t *reads)
{
    if (writes) {
        *writes = s_buffer_write_count;
    }
    if (reads) {
        *reads = s_buffer_read_count;
    }
    return s_buffer_write_count + s_buffer_read_count;
}
