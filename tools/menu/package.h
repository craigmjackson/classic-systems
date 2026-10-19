#ifndef PACKAGE_H
#define PACKAGE_H

#define MAX_PACKAGES  48
#define PKG_NAME_LEN  40
#define PKG_EXE_LEN   48
#define PKG_ARGS_LEN  64
#define PKG_DIR_LEN   13

typedef struct {
    char dirname[PKG_DIR_LEN];   /* 8.3 directory name under the root */
    char name[PKG_NAME_LEN];     /* friendly name                   */
    char exe[PKG_EXE_LEN];       /* file to run (.bat/.exe/.com)    */
    char args[PKG_ARGS_LEN];     /* arguments, whitespace-split     */
} package_t;

/* Scan root for subdirectories containing PACKAGE.INI. Returns count,
 * list sorted case-insensitively by friendly name. */
int pkg_scan(const char *root, package_t *list, int max);

/* Write <root>\<dir>\PACKAGE.INI with the three canonical keys. */
int pkg_save(const package_t *pkg, const char *root);

/* Remove <root>\<dir>\PACKAGE.INI. Returns 1 on success. */
int pkg_delete_ini(const package_t *pkg, const char *root);

/* mkdir <root>\<dir> (if needed) and write its PACKAGE.INI. */
int pkg_create(const char *root, const package_t *pkg);

/* Recursively delete every file and subdirectory under
 * <root>\<dir>, then remove the directory itself. */
int pkg_delete_tree(const package_t *pkg, const char *root);

/* Run the package with CWD set to its directory. .com/.exe are EXEC'd
 * directly; everything else (incl. .bat) goes through %COMSPEC% /c -
 * DOS would EXEC a non-binary as machine code and crash. Returns the
 * child's exit code, -1 on spawn failure, -2 on a usage error. Message
 * for both error cases is copied into errbuf. Caller must endwin(). */
int pkg_run(const package_t *pkg, const char *root,
            char *errbuf, int errlen);

/* 1 if s is a legal DOS 8.3 directory name (1-8 chars, alnum/-/_). */
int pkg_is_valid_dirname(const char *s);

#endif
