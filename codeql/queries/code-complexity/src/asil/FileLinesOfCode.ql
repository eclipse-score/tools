/**
 * @name High Lines of Code in Files (ASIL)
 * @description Files exceeding the lines-of-code threshold of the ASIL profile
 * @kind problem
 * @id score/cpp/asil/lines-of-code-in-files
 * @problem.severity warning
 * @tags maintainability readability
 */

import cpp
import lib.Checks
import lib.Thresholds

int getFileLinesOfCodeThreshold() { result = Asil::linesOfCodePerFile() }

from File f, int loc
where Checks::fileLinesOfCode(f, loc, getFileLinesOfCodeThreshold())
select f,
  "File has " + loc + " lines of code, exceeding threshold of " +
    getFileLinesOfCodeThreshold()
