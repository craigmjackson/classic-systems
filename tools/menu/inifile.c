#include <stdio.h>
#include <string.h>
#include "inifile.h"

static void trim(char *s)
{
    char *p;
    size_t n;

    n = strlen(s);
    while (n > 0 && (s[n - 1] == ' ' || s[n - 1] == '\t' ||
                     s[n - 1] == '\r' || s[n - 1] == '\n')) {
        s[--n] = '\0';
    }
    p = s;
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (p != s) {
        memmove(s, p, strlen(p) + 1);
    }
}

int ini_get(const char *path, const char *section, const char *key,
            char *out, int outlen)
{
    FILE *f;
    char line[256];
    char keybuf[64];
    char *eq;
    size_t klen;
    int in_sect;

    out[0] = '\0';
    klen = strlen(key);
    in_sect = 0;

    f = fopen(path, "rb");
    if (!f) {
        return 0;
    }
    while (fgets(line, sizeof line, f)) {
        trim(line);
        if (line[0] == '\0' || line[0] == ';' || line[0] == '#') {
            continue;
        }
        if (line[0] == '[') {
            char *rb = strchr(line, ']');
            if (rb) {
                *rb = '\0';
                in_sect = (stricmp(line + 1, section) == 0);
            } else {
                in_sect = 0;
            }
            continue;
        }
        if (!in_sect) {
            continue;
        }
        eq = strchr(line, '=');
        if (!eq) {
            continue;
        }
        *eq = '\0';
        strncpy(keybuf, line, sizeof keybuf - 1);
        keybuf[sizeof keybuf - 1] = '\0';
        trim(keybuf);
        if (strlen(keybuf) == klen && stricmp(keybuf, key) == 0) {
            strncpy(out, eq + 1, outlen - 1);
            out[outlen - 1] = '\0';
            trim(out);
            fclose(f);
            return 1;
        }
    }
    fclose(f);
    return 0;
}
