#include "registry.h"

#include <string.h>

static const registry_entry_t s_builtin_entries[] = {
    { "echo", 0u, 0 },
    { "stats", 4u, 0 },
};

static size_t s_registry_total = 0u;

void registry_init(registry_t *registry, buffer_t *sink)
{
    if (!registry) {
        return;
    }
    memset(registry, 0, sizeof(*registry));
    registry->sink = sink;
}

int registry_add(registry_t *registry, const char *name, size_t offset,
                 buf_status_t (*handler)(buffer_t *, void *))
{
    registry_entry_t *slot;

    if (!registry || !name) {
        return -1;
    }
    if (registry->count >= REGISTRY_MAX_ENTRIES) {
        return -2;
    }
    slot = &registry->entries[registry->count];
    slot->name = name;
    slot->offset = offset;
    slot->handler = handler;
    registry->count++;
    s_registry_total++;
    return (int)registry->count;
}

size_t registry_size(const registry_t *registry)
{
    return registry ? registry->count : 0u;
}

buf_status_t registry_dispatch(registry_t *registry, const char *name, void *user)
{
    size_t i;

    if (!registry || !name) {
        return BUF_ERR_EMPTY;
    }
    for (i = 0u; i < registry->count; i++) {
        registry_entry_t *entry = &registry->entries[i];
        if (strcmp(entry->name, name) == 0) {
            if (entry->handler) {
                return entry->handler(registry->sink, user);
            }
            return BUF_OK;
        }
    }
    return BUF_ERR_EMPTY;
}

size_t registry_builtin_count(void)
{
    return sizeof(s_builtin_entries) / sizeof(s_builtin_entries[0]);
}
