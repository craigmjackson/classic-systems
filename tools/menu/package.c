#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <dos.h>
#include <direct.h>
#include <process.h>
#include "inifile.h"
#include "package.h"

static int cmp_pkg(const void *a, const void *b)
{
    const package_t *pa = (const package_t *)a;
    const package_t *pb = (const package_t *)b;
    return stricmp(pa->name, pb->name);
}

static void pkg_path(char *out, int outlen, const char *root,
                     const char *dirname, const char *file)
{
    if (file) {
        sprintf(out, "%s\\%s\\%s", root, dirname, file);
    } else {
        sprintf(out, "%s\\%s", root, dirname);
    }
}

static int load_pkg(const char *root, const char *dirname, package_t *pkg)
{
    char path[_MAX_PATH];

    pkg_path(path, sizeof path, root, dirname, "PACKAGE.INI");
    memset(pkg, 0, sizeof *pkg);
    strncpy(pkg->dirname, dirname, PKG_DIR_LEN - 1);
    if (!ini_get(path, "package", "name", pkg->name, PKG_NAME_LEN)) {
        return 0;
    }
    ini_get(path, "package", "executable", pkg->exe, PKG_EXE_LEN);
    ini_get(path, "package", "args", pkg->args, PKG_ARGS_LEN);
    return 1;
}

int pkg_scan(const char *root, package_t *list, int max)
{
    struct find_t ff;
    char pat[_MAX_PATH];
    int n;

    n = 0;
    sprintf(pat, "%s\\*", root);
    if (_dos_findfirst(pat, _A_SUBDIR, &ff) != 0) {
        return 0;
    }
    do {
        if (!(ff.attrib & _A_SUBDIR)) {
            continue;
        }
        if (strcmp(ff.name, ".") == 0 || strcmp(ff.name, "..") == 0) {
            continue;
        }
        if (n < max && load_pkg(root, ff.name, &list[n])) {
            n++;
        }
    } while (_dos_findnext(&ff) == 0);
    _dos_findclose(&ff);
    qsort(list, (size_t)n, sizeof(package_t), cmp_pkg);
    return n;
}

int pkg_save(const package_t *pkg, const char *root)
{
    char path[_MAX_PATH];
    FILE *f;

    pkg_path(path, sizeof path, root, pkg->dirname, "PACKAGE.INI");
    f = fopen(path, "wb");
    if (!f) {
        return 0;
    }
    fprintf(f, "[package]\r\n");
    fprintf(f, "name=%s\r\n", pkg->name);
    fprintf(f, "executable=%s\r\n", pkg->exe);
    fprintf(f, "args=%s\r\n", pkg->args);
    fclose(f);
    return 1;
}

int pkg_delete_ini(const package_t *pkg, const char *root)
{
    char path[_MAX_PATH];

    pkg_path(path, sizeof path, root, pkg->dirname, "PACKAGE.INI");
    return remove(path) == 0;
}

int pkg_create(const char *root, const package_t *pkg)
{
    char path[_MAX_PATH];

    pkg_path(path, sizeof path, root, pkg->dirname, NULL);
    if (_mkdir(path) != 0 && errno != EEXIST) {
        return 0;
    }
    return pkg_save(pkg, root);
}

static void del_tree(const char *path)
{
    struct find_t ff;
    char pat[_MAX_PATH];
    char sub[_MAX_PATH];

    sprintf(pat, "%s\\*", path);
    if (_dos_findfirst(pat, _A_SUBDIR, &ff) == 0) {
        do {
            if (strcmp(ff.name, ".") == 0 || strcmp(ff.name, "..") == 0) {
                continue;
            }
            sprintf(sub, "%s\\%s", path, ff.name);
            if (ff.attrib & _A_SUBDIR) {
                del_tree(sub);
            } else {
                remove(sub);
            }
        } while (_dos_findnext(&ff) == 0);
        _dos_findclose(&ff);
    }
    _rmdir(path);
}

int pkg_delete_tree(const package_t *pkg, const char *root)
{
    char path[_MAX_PATH];

    pkg_path(path, sizeof path, root, pkg->dirname, NULL);
    del_tree(path);
    return 1;
}

int pkg_run(const package_t *pkg, const char *root,
            char *errbuf, int errlen)
{
    char cwd[_MAX_PATH];
    char dir[_MAX_PATH];
    char argcopy[PKG_ARGS_LEN + 2];
    const char *argv[20];
    char *p;
    const char *cs;
    int n;
    int i;
    int total;
    int via_comspec;
    int rc;
    size_t elen;
    const char *ext;

    errbuf[0] = '\0';
    if (!pkg->exe[0]) {
        strncpy(errbuf, "No executable configured (press E to edit).",
                errlen - 1);
        errbuf[errlen - 1] = '\0';
        return -2;
    }

    elen = strlen(pkg->exe);
    ext = (elen >= 4) ? pkg->exe + elen - 4 : "";
    via_comspec = !(ext[0] == '.' &&
                    (!stricmp(ext, ".com") || !stricmp(ext, ".exe")));

    cs = NULL;
    n = 0;
    if (via_comspec) {
        cs = getenv("COMSPEC");
        if (!cs || !cs[0]) {
            cs = "C:\\COMMAND.COM";
        }
        argv[n++] = cs;
        argv[n++] = "/c";
        argv[n++] = pkg->exe;
    } else {
        argv[n++] = pkg->exe;
    }
    strncpy(argcopy, pkg->args, sizeof argcopy - 1);
    argcopy[sizeof argcopy - 1] = '\0';
    p = strtok(argcopy, " \t");
    while (p && n < 18) {
        argv[n++] = p;
        p = strtok(NULL, " \t");
    }
    argv[n] = NULL;

    total = 0;
    for (i = 0; i < n; i++) {
        total += (int)strlen(argv[i]) + 1;
    }
    if (total > 120) {
        strncpy(errbuf, "Command line too long for DOS (128 byte limit).",
                errlen - 1);
        errbuf[errlen - 1] = '\0';
        return -2;
    }

    cwd[0] = '\0';
    getcwd(cwd, sizeof cwd);
    pkg_path(dir, sizeof dir, root, pkg->dirname, NULL);
    if (chdir(dir) != 0) {
        sprintf(errbuf, "Cannot enter %s", dir);
        return -2;
    }

    if (via_comspec) {
        rc = spawnv(P_WAIT, cs, argv);
    } else {
        rc = spawnv(P_WAIT, pkg->exe, argv);
    }
    if (rc == -1) {
        sprintf(errbuf, "Cannot run %s (errno %d)", pkg->exe, errno);
    }

    if (cwd[0]) {
        chdir(cwd);
    }
    return rc;
}

int pkg_is_valid_dirname(const char *s)
{
    int i;
    int n;

    n = (int)strlen(s);
    if (n < 1 || n > 8) {
        return 0;
    }
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!(isalnum(c) || c == '-' || c == '_')) {
            return 0;
        }
    }
    return 1;
}
