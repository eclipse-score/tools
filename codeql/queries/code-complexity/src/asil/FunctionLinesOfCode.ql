/**
 * @name High Lines of Code per Function (ASIL)
 * @description Functions exceeding the lines-of-code threshold of the ASIL profile
 * @kind problem
 * @id score/cpp/asil/lines-of-code-per-function
 * @problem.severity warning
 * @tags maintainability readability
 */

import cpp
import lib.Checks
import lib.Thresholds

int getLinesOfCodeThreshold() { result = Asil::linesOfCodePerFunction() }

from Function f, int loc
where Checks::functionLinesOfCode(f, loc, getLinesOfCodeThreshold())
select f,
  "Function has " + loc + " lines of code, exceeding threshold of " +
    getLinesOfCodeThreshold()
