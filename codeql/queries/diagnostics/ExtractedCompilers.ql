/**
 * @name Extracted compilers
 * @description Compilers that produced the compilations stored in the database.
 *              The extractor is started as `extractor --mimic <compiler> <args...>`,
 *              so the compiler identity is the argument following `--mimic`.
 * @kind table
 * @id score/cpp/extracted-compilers
 */

import cpp

from Compilation c, string compiler
where c.getArgument(1) = "--mimic" and compiler = c.getArgument(2)
select compiler,
  count(Compilation c2 | c2.getArgument(2) = compiler and c2.getArgument(1) = "--mimic")
