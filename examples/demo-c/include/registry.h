#ifndef DEMO_REGISTRY_H
#define DEMO_REGISTRY_H

#include "buffer.h"

#define REGISTRY_MAX_ENTRIES 16u

typedef struct registry_entry {
    const char *name;
    size_t offset;
    buf_status_t (*handler)(buffer_t *buf, void *user);
} registry_entry_t;

typedef struct registry {
    registry_entry_t entries[REGISTRY_MAX_ENTRIES];
    size_t count;
    buffer_t *sink;
} registry_t;

void registry_init(registry_t *registry, buffer_t *sink);
int registry_add(registry_t *registry, const char *name, size_t offset,
                 buf_status_t (*handler)(buffer_t *, void *));
buf_status_t registry_dispatch(registry_t *registry, const char *name, void *user);
size_t registry_size(const registry_t *registry);

#endif
