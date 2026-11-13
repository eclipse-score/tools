/**
 * @name High Lines of Code per Function (QM)
 * @description Functions exceeding the lines-of-code threshold of the QM profile
 * @kind problem
 * @id score/cpp/qm/lines-of-code-per-function
 * @problem.severity warning
 * @tags maintainability readability
 */

import cpp
import lib.Checks
import lib.Thresholds

int getLinesOfCodeThreshold() { result = Qm::linesOfCodePerFunction() }

from Function f, int loc
where Checks::functionLinesOfCode(f, loc, getLinesOfCodeThreshold())
select f,
  "Function has " + loc + " lines of code, exceeding threshold of " +
    getLinesOfCodeThreshold()
