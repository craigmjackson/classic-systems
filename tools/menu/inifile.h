#ifndef INIFILE_H
#define INIFILE_H

/* Read key from [section] of an INI-style file. Returns 1 on success,
 * 0 if file/key not found. Value is trimmed and copied to out. */
int ini_get(const char *path, const char *section, const char *key,
            char *out, int outlen);

#endif
