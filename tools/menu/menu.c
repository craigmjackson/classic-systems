/* classic-systems DOS menu launcher (PDCurses / Open Watcom, 16-bit)
 *
 * Scans F:\GAMES and F:\APPS for directories containing PACKAGE.INI
 * ([package] name= / executable= / args=) and presents a two-level
 * text UI. Packages can be run, edited, added, and deleted from
 * within the tool. Full deletion requires typing DELETE to confirm.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <conio.h>
#include <io.h>
#include <curses.h>
#include "package.h"

#define GAMES_ROOT "F:\\GAMES"
#define APPS_ROOT  "F:\\APPS"

#define PR_BG    1
#define PR_SEL   2

#define MAIN_GAMES 0
#define MAIN_APPS  1
#define MAIN_QUIT  2

static package_t games[MAX_PACKAGES];
static package_t apps[MAX_PACKAGES];
static int ngames;
static int napps;

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */

static void reload(int which)
{
    if (which == MAIN_APPS) {
        napps = pkg_scan(APPS_ROOT, apps, MAX_PACKAGES);
    } else {
        ngames = pkg_scan(GAMES_ROOT, games, MAX_PACKAGES);
    }
}

static package_t *list_of(int which, int *count, const char **root)
{
    if (which == MAIN_APPS) {
        *count = napps;
        *root = APPS_ROOT;
        return apps;
    }
    *count = ngames;
    *root = GAMES_ROOT;
    return games;
}

/* Simple message box; any key dismisses. msg may contain \n lines. */
static void msg_box(const char *title, const char *msg)
{
    WINDOW *w;
    int h, wd, y, x, i, len;
    const char *p, *nl;
    char line[80];

    h = 5;
    wd = 0;
    p = msg;
    while (*p) {
        nl = strchr(p, '\n');
        len = nl ? (int)(nl - p) : (int)strlen(p);
        if (len > wd) {
            wd = len;
        }
        p = nl ? nl + 1 : p + len;
    }
    if (wd < (int)strlen(title) + 2) {
        wd = (int)strlen(title) + 2;
    }
    wd += 4;
    if (wd > COLS - 2) {
        wd = COLS - 2;
    }
    y = (LINES - h) / 2;
    x = (COLS - wd) / 2;
    w = newwin(h, wd, y, x);
    if (!w) {
        return;
    }
    wbkgd(w, COLOR_PAIR(PR_BG));
    box(w, 0, 0);
    mvwprintw(w, 0, 2, " %s ", title);
    i = 1;
    p = msg;
    while (*p && i <= h - 3) {
        nl = strchr(p, '\n');
        if (nl) {
            len = (int)(nl - p);
            if (len > wd - 4) {
                len = wd - 4;
            }
            strncpy(line, p, (size_t)len);
            line[len] = '\0';
            mvwprintw(w, i, 2, "%s", line);
            p = nl + 1;
        } else {
            mvwprintw(w, i, 2, "%.*s", wd - 4, p);
            break;
        }
        i++;
    }
    mvwprintw(w, h - 2, 2, "Press any key...");
    wrefresh(w);
    delwin(w);
    getch();
}

/* Single-line field editor. Returns the key that finished editing:
 * '\n' (Enter), '\t' (Tab), 27 (Esc), or KEY_UP/KEY_DOWN/KEY_BTAB
 * (field navigation; caller decides what to do with them). */
static int edit_field(WINDOW *w, int y, int x, int width,
                      char *buf, int buflen)
{
    int len;
    int pos;
    int ch;

    len = (int)strlen(buf);
    pos = len;
    curs_set(1);
    for (;;) {
        mvwhline(w, y, x, ' ', width);
        mvwprintw(w, y, x, "%.*s", width, buf);
        wmove(w, y, x + pos);
        wrefresh(w);
        ch = wgetch(w);
        if (ch == '\n' || ch == KEY_ENTER || ch == '\t' || ch == 27 ||
            ch == KEY_UP || ch == KEY_DOWN || ch == KEY_BTAB) {
            break;
        }
        if ((ch == KEY_BACKSPACE || ch == 8 || ch == 127) && pos > 0) {
            memmove(buf + pos - 1, buf + pos, (size_t)(len - pos + 1));
            pos--;
            len--;
        } else if (ch == KEY_LEFT && pos > 0) {
            pos--;
        } else if (ch == KEY_RIGHT && pos < len) {
            pos++;
        } else if (ch == KEY_HOME) {
            pos = 0;
        } else if (ch == KEY_END) {
            pos = len;
        } else if (ch >= 32 && ch < 127 && len < buflen - 1) {
            memmove(buf + pos + 1, buf + pos, (size_t)(len - pos + 1));
            buf[pos] = (char)ch;
            pos++;
            len++;
        }
    }
    curs_set(0);
    return ch;
}

/* -------------------------------------------------------------------------
 * Add / edit dialog
 * ---------------------------------------------------------------------- */

#define DLG_CANCEL 0
#define DLG_OK     1

static int pkg_dialog(int add_mode, const char *root, package_t *pkg)
{
    static const char *add_labels[4] =
        { "Directory ", "Name      ", "Executable", "Args      " };
    static const char *edit_labels[3] =
        { "Name      ", "Executable", "Args      " };
    char dirbuf[PKG_DIR_LEN];
    char *values[4];
    int caps[4];
    const char **labels;
    int nf, h, w, y, x;
    int i, ch, rc;
    WINDOW *dlg;

    nf = add_mode ? 4 : 3;
    labels = add_mode ? add_labels : edit_labels;
    h = nf * 2 + 4;
    w = 58;
    y = (LINES - h) / 2;
    x = (COLS - w) / 2;

    dirbuf[0] = '\0';
    if (add_mode) {
        values[0] = dirbuf;
        values[1] = pkg->name;
        values[2] = pkg->exe;
        values[3] = pkg->args;
        caps[0] = PKG_DIR_LEN;
        caps[1] = PKG_NAME_LEN;
        caps[2] = PKG_EXE_LEN;
        caps[3] = PKG_ARGS_LEN;
    } else {
        values[0] = pkg->name;
        values[1] = pkg->exe;
        values[2] = pkg->args;
        caps[0] = PKG_NAME_LEN;
        caps[1] = PKG_EXE_LEN;
        caps[2] = PKG_ARGS_LEN;
    }

    dlg = newwin(h, w, y, x);
    if (!dlg) {
        return DLG_CANCEL;
    }
    wbkgd(dlg, COLOR_PAIR(PR_BG));
    keypad(dlg, TRUE);
    box(dlg, 0, 0);
    mvwprintw(dlg, 0, 2, add_mode ? " Add package " : " Edit package ");
    if (add_mode) {
        mvwprintw(dlg, 1, 2, "under %s (8.3 name)", root);
    }

    rc = DLG_CANCEL;
    i = 0;
    for (;;) {
        int j;
        for (j = 0; j < nf; j++) {
            int fy = 2 + j * 2;
            mvwprintw(dlg, fy, 2, "%s:", labels[j]);
            mvwhline(dlg, fy, 14, ' ', w - 18);
            mvwprintw(dlg, fy, 14, "%.*s", w - 18, values[j]);
        }
        mvwprintw(dlg, h - 1, 2,
                  " Enter Next/Save  Tab/Arrows Move  Esc Cancel ");
        ch = edit_field(dlg, 2 + i * 2, 14, w - 18, values[i], caps[i]);
        if (ch == 27) {
            rc = DLG_CANCEL;
            break;
        }
        if (ch == KEY_UP || ch == KEY_BTAB) {
            i = (i + nf - 1) % nf;
            continue;
        }
        if (ch == '\t' || ch == KEY_DOWN ||
            ((ch == '\n' || ch == KEY_ENTER) && i < nf - 1)) {
            i = (i + 1) % nf;
            continue;
        }
        /* Enter on the last field: accept */
        rc = DLG_OK;
        break;
    }
    delwin(dlg);
    touchwin(stdscr);
    refresh();

    if (rc != DLG_OK) {
        return DLG_CANCEL;
    }
    if (add_mode) {
        strncpy(pkg->dirname, dirbuf, PKG_DIR_LEN - 1);
        pkg->dirname[PKG_DIR_LEN - 1] = '\0';
    }
    return DLG_OK;
}

/* -------------------------------------------------------------------------
 * Run / edit / add / delete actions
 * ---------------------------------------------------------------------- */

static void run_pkg(const package_t *p, const char *root)
{
    char err[80];
    int rc;

    endwin();
    rc = pkg_run(p, root, err, sizeof err);
    if (rc < 0) {
        printf("\n%s\n\nPress any key to return to menu...\n", err);
        getch();
    }
    refresh();
    clearok(stdscr, TRUE);
    erase();
    refresh();
}

static void do_edit(package_t *p, const char *root)
{
    package_t tmp;

    tmp = *p;
    if (pkg_dialog(0, root, &tmp) == DLG_OK) {
        if (!tmp.name[0]) {
            msg_box("Error", "Name cannot be empty.");
            return;
        }
        if (!pkg_save(&tmp, root)) {
            msg_box("Error", "Could not write PACKAGE.INI.");
            return;
        }
        *p = tmp;
    }
}

static void do_add(const char *root)
{
    package_t pkg;
    char path[_MAX_PATH];

    memset(&pkg, 0, sizeof pkg);
    if (pkg_dialog(1, root, &pkg) != DLG_OK) {
        return;
    }
    if (!pkg_is_valid_dirname(pkg.dirname)) {
        msg_box("Error",
                "Directory must be 1-8 chars:\nletters, digits, - or _");
        return;
    }
    if (!pkg.name[0]) {
        msg_box("Error", "Name cannot be empty.");
        return;
    }
    sprintf(path, "%s\\%s", root, pkg.dirname);
    if (_access(path, 0) == 0) {
        msg_box("Error", "Directory already exists.");
        return;
    }
    if (!pkg_create(root, &pkg)) {
        msg_box("Error", "Could not create package directory.");
        return;
    }
}

static void do_delete(package_t *p, const char *root)
{
    int ch;
    char prompt[128];
    char confirm[16];
    WINDOW *w;
    int h = 11, wd = 56, y, x;

    /* First dialog: choose scope */
    y = (LINES - h) / 2;
    x = (COLS - wd) / 2;
    w = newwin(h, wd, y, x);
    if (!w) {
        return;
    }
    wbkgd(w, COLOR_PAIR(PR_BG));
    box(w, 0, 0);
    mvwprintw(w, 0, 2, " Delete \"%s\" ", p->name);
    mvwprintw(w, 2, 2, "Y  Remove from menu (keep files)");
    mvwprintw(w, 3, 2, "D  Delete package and ALL FILES");
    mvwprintw(w, 5, 2, "   under %s\\%s", root, p->dirname);
    mvwprintw(w, h - 2, 2, " Y/D  or  Esc cancel ");
    wrefresh(w);
    ch = wgetch(w);
    delwin(w);
    touchwin(stdscr);
    refresh();

    if (ch == 'y' || ch == 'Y') {
        if (!pkg_delete_ini(p, root)) {
            msg_box("Error", "Could not delete PACKAGE.INI.");
        }
        return;
    }
    if (ch != 'd' && ch != 'D') {
        return;
    }

    /* Second dialog: extra confirmation, must type DELETE */
    sprintf(prompt, "%s\\%s", root, p->dirname);
    h = 11;
    wd = 56;
    y = (LINES - h) / 2;
    x = (COLS - wd) / 2;
    w = newwin(h, wd, y, x);
    if (!w) {
        return;
    }
    wbkgd(w, COLOR_PAIR(PR_BG));
    keypad(w, TRUE);
    box(w, 0, 0);
    mvwprintw(w, 0, 2, " Confirm FULL delete ");
    mvwprintw(w, 2, 2, "This removes every file and directory");
    mvwprintw(w, 3, 2, "under:");
    mvwprintw(w, 4, 4, "%s", prompt);
    mvwprintw(w, 6, 2, "Type DELETE to confirm:");
    confirm[0] = '\0';
    wrefresh(w);
    for (;;) {
        ch = edit_field(w, 7, 4, 20, confirm, sizeof confirm);
        if (ch != KEY_UP && ch != KEY_DOWN && ch != KEY_BTAB) {
            break;
        }
    }
    delwin(w);
    touchwin(stdscr);
    refresh();

    if (strcmpi(confirm, "DELETE") != 0) {
        msg_box("Aborted", "Full delete cancelled.");
        return;
    }
    pkg_delete_tree(p, root);
}

/* -------------------------------------------------------------------------
 * List screen
 * ---------------------------------------------------------------------- */

static void list_screen(int which)
{
    package_t *list;
    int count;
    const char *root;
    const char *title;
    int sel, top, ch, i;
    int h, w, y, x, rows;
    WINDOW *win;

    list = list_of(which, &count, &root);
    title = (which == MAIN_APPS) ? " Apps - F:\\APPS " : " Games - F:\\GAMES ";
    h = 20;
    w = 64;
    y = (LINES - h) / 2;
    x = (COLS - w) / 2;
    rows = h - 4;
    sel = 0;
    top = 0;

    win = newwin(h, w, y, x);
    if (!win) {
        return;
    }
    wbkgd(win, COLOR_PAIR(PR_BG));
    keypad(win, TRUE);

    for (;;) {
        list = list_of(which, &count, &root);
        werase(win);
        box(win, 0, 0);
        mvwprintw(win, 0, 2, "%s", title);
        if (count == 0) {
            mvwprintw(win, 3, 4, "No packages found.");
            mvwprintw(win, 4, 4, "Press Ins to add one.");
        } else {
            if (sel >= count) {
                sel = count - 1;
            }
            if (sel < top) {
                top = sel;
            }
            if (sel >= top + rows) {
                top = sel - rows + 1;
            }
            for (i = 0; i < rows && top + i < count; i++) {
                package_t *p = &list[top + i];
                if (top + i == sel) {
                    wattrset(win, COLOR_PAIR(PR_SEL));
                }
                mvwprintw(win, 2 + i, 1, " %-30.30s %-10.10s %-16.16s",
                          p->name, p->dirname, p->exe);
                if (top + i == sel) {
                    wattrset(win, COLOR_PAIR(PR_BG));
                }
            }
        }
        mvwhline(win, h - 2, 1, ACS_HLINE, w - 2);
        mvwprintw(win, h - 1, 1,
                  " \x18\x19 Sel Enter Run  E Edit  Ins Add  Del Del  Esc ");
        wrefresh(win);

        ch = wgetch(win);
        switch (ch) {
        case KEY_UP:
            if (count > 0) {
                sel = (sel + count - 1) % count;
            }
            break;
        case KEY_DOWN:
            if (count > 0) {
                sel = (sel + 1) % count;
            }
            break;
        case KEY_PPAGE:
            sel -= rows;
            if (sel < 0) {
                sel = 0;
            }
            break;
        case KEY_NPAGE:
            sel += rows;
            if (sel > count - 1) {
                sel = count > 0 ? count - 1 : 0;
            }
            break;
        case KEY_HOME:
            sel = 0;
            break;
        case KEY_END:
            sel = count > 0 ? count - 1 : 0;
            break;
        case '\n':
        case KEY_ENTER:
            if (count > 0) {
                run_pkg(&list[sel], root);
            }
            break;
        case 'e':
        case 'E':
            if (count > 0) {
                do_edit(&list[sel], root);
                reload(which);
            }
            break;
        case KEY_IC:
        case 'a':
        case 'A':
            do_add(root);
            reload(which);
            if (count > 0 && sel >= count) {
                sel = count - 1;
            }
            break;
        case KEY_DC:
            if (count > 0) {
                do_delete(&list[sel], root);
                reload(which);
                if (count > 0 && sel >= count) {
                    sel = count - 1;
                } else if (count == 0) {
                    sel = 0;
                }
            }
            break;
        case 27:
            delwin(win);
            return;
        }
    }
}

/* -------------------------------------------------------------------------
 * Main screen
 * ---------------------------------------------------------------------- */

static void draw_main(int sel)
{
    int h, w, y, x;
    WINDOW *win;

    h = 10;
    w = 34;
    y = (LINES - h) / 2;
    x = (COLS - w) / 2;
    win = newwin(h, w, y, x);
    if (!win) {
        return;
    }
    wbkgd(win, COLOR_PAIR(PR_BG));
    box(win, 0, 0);
    mvwprintw(win, 0, 2, " classic-systems menu ");
    mvwhline(win, 2, 1, ACS_HLINE, w - 2);
    if (sel == MAIN_GAMES) {
        wattrset(win, COLOR_PAIR(PR_SEL));
    }
    mvwprintw(win, 4, 4, " Games  (%2d) ", ngames);
    wattrset(win, COLOR_PAIR(PR_BG));
    if (sel == MAIN_APPS) {
        wattrset(win, COLOR_PAIR(PR_SEL));
    }
    mvwprintw(win, 5, 4, " Apps   (%2d) ", napps);
    wattrset(win, COLOR_PAIR(PR_BG));
    if (sel == MAIN_QUIT) {
        wattrset(win, COLOR_PAIR(PR_SEL));
    }
    mvwprintw(win, 6, 4, " Quit          ");
    wattrset(win, COLOR_PAIR(PR_BG));
    mvwprintw(win, h - 2, 2, " \x18\x19 select  Enter ");
    wrefresh(win);
    delwin(win);
}

int main(void)
{
    int sel, ch;

    initscr();
    noecho();
    cbreak();
    curs_set(0);
    keypad(stdscr, TRUE);
    if (has_colors()) {
        start_color();
        init_pair(PR_BG, COLOR_WHITE, COLOR_BLUE);
        init_pair(PR_SEL, COLOR_BLACK, COLOR_CYAN);
    }
    wbkgd(stdscr, COLOR_PAIR(PR_BG));

    reload(MAIN_GAMES);
    reload(MAIN_APPS);

    sel = MAIN_GAMES;
    for (;;) {
        erase();
        refresh();
        draw_main(sel);
        ch = getch();
        if (ch == KEY_UP) {
            sel = (sel + 2) % 3;
        } else if (ch == KEY_DOWN) {
            sel = (sel + 1) % 3;
        } else if (ch == '\n' || ch == KEY_ENTER) {
            if (sel == MAIN_QUIT) {
                break;
            }
            list_screen(sel);
        } else if (ch == 'g' || ch == 'G') {
            list_screen(MAIN_GAMES);
        } else if (ch == 'a' || ch == 'A') {
            list_screen(MAIN_APPS);
        } else if (ch == 'q' || ch == 'Q') {
            break;
        }
    }

    endwin();
    return 0;
}
