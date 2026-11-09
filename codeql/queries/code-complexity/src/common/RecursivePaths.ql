/**
 * @name Number of Recursive Paths in Functions
 * @description Functions with recursive call paths exceeding threshold
 * @kind problem
 * @id score/cpp/recursive-paths
 * @problem.severity warning
 * @tags maintainability complexity recursion
 */

import cpp
import lib.Checks

int getRecursivePathsThreshold() { result = 0 }

from Function f, int recursivePaths
where Checks::functionRecursivePaths(f, recursivePaths, getRecursivePathsThreshold())
select f,
  "Function has " + recursivePaths + " recursive paths, exceeding threshold of " +
    getRecursivePathsThreshold()
