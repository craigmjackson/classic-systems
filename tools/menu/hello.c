/* Tiny demo program for the apps menu (built as HELLO.EXE). */
#include <stdio.h>
#include <conio.h>

int main(int argc, char **argv)
{
    printf("\n  HELLO from an APP!  arg=%s\n\n",
           argc > 1 ? argv[1] : "(none)");
    printf("  Press any key...\n");
    getch();
    return 0;
}
