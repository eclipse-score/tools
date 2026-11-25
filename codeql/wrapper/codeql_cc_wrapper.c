/********************************************************************************
 * Copyright (c) 2026 Contributors to the Eclipse Foundation
 *
 * See the NOTICE file(s) distributed with this work for additional
 * information regarding copyright ownership.
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/*
 * Dynamic launcher that forwards to the program named by CODEQL_CC_WRAPPER_TARGET.
 *
 * The CodeQL tracer works through LD_PRELOAD, which cannot hook statically linked
 * compilers. Starting such a compiler through this dynamically linked binary lets
 * the tracer observe the exec and attach to the compilation.
 */

#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum
{
    EXIT_EXEC_FAILED = 127
};

int main(int argc, char** argv)
{
    const char* target = getenv("CODEQL_CC_WRAPPER_TARGET");
    char** forward_argv = NULL;

    if (target == NULL || target[0] == '\0')
    {
        (void)fputs("codeql_cc_wrapper: CODEQL_CC_WRAPPER_TARGET is not set\n", stderr);
        return 2;
    }

    forward_argv = (char**)calloc((size_t)argc + 1, sizeof(*forward_argv));
    if (forward_argv == NULL)
    {
        (void)fputs("codeql_cc_wrapper: calloc failed: ", stderr);
        (void)fputs(strerror(errno), stderr);
        (void)fputs("\n", stderr);
        return 2;
    }

    forward_argv[0] = (char*)target;
    for (int index = 1; index < argc; ++index)
    {
        forward_argv[index] = argv[index];
    }
    forward_argv[argc] = NULL;

    execv(target, forward_argv);

    // if replacing the process image failed, print an error message and exit with a specific code
    (void)fputs("codeql_cc_wrapper: execv(", stderr);
    (void)fputs(target, stderr);
    (void)fputs(") failed: ", stderr);
    (void)fputs(strerror(errno), stderr);
    (void)fputs("\n", stderr);
    free((void*)forward_argv);
    return EXIT_EXEC_FAILED;
}
