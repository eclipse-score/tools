/**
 * @name High Lines of Code in Files (QM)
 * @description Files exceeding the lines-of-code threshold of the QM profile
 * @kind problem
 * @id score/cpp/qm/lines-of-code-in-files
 * @problem.severity warning
 * @tags maintainability readability
 */

import cpp
import lib.Checks
import lib.Thresholds

int getFileLinesOfCodeThreshold() { result = Qm::linesOfCodePerFile() }

from File f, int loc
where Checks::fileLinesOfCode(f, loc, getFileLinesOfCodeThreshold())
select f,
  "File has " + loc + " lines of code, exceeding threshold of " +
    getFileLinesOfCodeThreshold()
