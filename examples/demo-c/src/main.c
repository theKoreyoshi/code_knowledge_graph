#include "buffer.h"
#include "registry.h"

#include <stdio.h>
#include <string.h>

static uint8_t s_storage[BUF_ALIGN4(BUF_DEFAULT_CAPACITY)];
static buffer_t s_buffer;
static registry_t s_registry;
static int s_errors = 0;

static int console_write(void *ctx, const uint8_t *data, size_t len)
{
    buffer_t *buf = (buffer_t *)ctx;
    fwrite(data, 1u, len, stdout);
    return (int)buffer_pending(buf);
}

static int console_flush(void *ctx)
{
    (void)ctx;
    fflush(stdout);
    return 0;
}

static buf_status_t handle_echo(buffer_t *buf, void *user)
{
    const char *text = (const char *)user;
    size_t len;

    if (!text) {
        s_errors++;
        return BUF_ERR_EMPTY;
    }
    len = strlen(text);
    if (buffer_write(buf, (const uint8_t *)text, len) != len) {
        s_errors++;
        return BUF_ERR_FULL;
    }
    return BUF_OK;
}

int main(void)
{
    static const buf_ops_t ops = { console_write, console_flush };
    uint8_t scratch[BUF_ALIGN4(16u)];
    size_t pending;

    buffer_init(&s_buffer, s_storage, sizeof(s_storage));
    buffer_set_ops(&s_buffer, &ops);
    registry_init(&s_registry, &s_buffer);

    registry_add(&s_registry, "echo", 0u, handle_echo);
    registry_add(&s_registry, "stats", 4u, 0);

    registry_dispatch(&s_registry, "echo", (void *)"hello");
    pending = buffer_pending(&s_buffer);
    buffer_read(&s_buffer, scratch, sizeof(scratch));

    console_flush(&s_buffer);
    return (s_errors == 0 && pending > 0u) ? 0 : 1;
}
