/**
 * Metric checks shared by the `QM` and `ASIL` query sets.
 *
 * Every predicate takes the threshold as an input (`bindingset`) so the profile
 * specific queries in `src/qm` and `src/asil` only differ in the threshold module
 * they read (`lib/Thresholds.qll`). A violation is reported when a metric is
 * *strictly greater* than the threshold.
 */

import cpp

module Checks {
  /** `f` is a function of the analysed sources with a body, not an instantiation. */
  private predicate analyzedFunction(Function f) {
    strictcount(f.getEntryPoint()) = 1 and
    f.fromSource()
  }

  /**
   * `f` has `loc` lines of code, exceeding `limit`.
   *
   * This mirrors the original `score/code-complexity-queries` query and keeps its
   * behaviour for the ASIL profile.
   */
  bindingset[limit]
  predicate functionLinesOfCode(Function f, int loc, int limit) {
    analyzedFunction(f) and
    loc = f.getMetrics().getNumberOfLinesOfCode() and
    loc > limit
  }

  /** `file` has `loc` lines of code, exceeding `limit`. */
  bindingset[limit]
  predicate fileLinesOfCode(File file, int loc, int limit) {
    file.fromSource() and
    loc = file.getMetrics().getNumberOfLinesOfCode() and
    loc > limit
  }

  /** `f` has the cyclomatic complexity `complexity`, exceeding `limit`. */
  bindingset[limit]
  predicate functionCyclomaticComplexity(Function f, int complexity, int limit) {
    analyzedFunction(f) and
    complexity = f.getMetrics().getCyclomaticComplexity() and
    complexity > limit
  }

  /** `f` declares `params` parameters, exceeding `limit`. */
  bindingset[limit]
  predicate functionParameters(Function f, int params, int limit) {
    analyzedFunction(f) and
    params = f.getMetrics().getNumberOfParameters() and
    params > limit
  }

  /** `f` contains `gotos` goto statements, exceeding `limit`. */
  bindingset[limit]
  predicate functionGotoStatements(Function f, int gotos, int limit) {
    analyzedFunction(f) and
    gotos = count(GotoStmt g | g.getEnclosingFunction() = f) and
    gotos > limit
  }

  /** `f` has `paths` recursive call paths, exceeding `limit`. */
  bindingset[limit]
  predicate functionRecursivePaths(Function f, int paths, int limit) {
    analyzedFunction(f) and
    paths = numberOfRecursivePaths(f) and
    paths > limit
  }

  /**
   * Gets the number of recursive call paths through `f`. A recursive call path
   * is a function `g` that `f` calls directly and from which `f` can be reached
   * again through a (possibly empty) chain of calls. This covers both direct
   * recursion (`f` calls itself) and mutual recursion (`f` -> ... -> `f`).
   */
  int numberOfRecursivePaths(Function f) {
    result =
      count(Function g |
        f.calls(g) and
        g.calls*(f)
      )
  }

  /**
   * `c` declares `interfaces` public member functions, exceeding `limit`.
   *
   * Members of a `struct` (or of a class before the first access specifier) are
   * public without an explicit `public` specifier, so "public interface" is
   * modelled as "neither private nor protected". Compiler-generated members
   * (implicit copy/move constructors, assignment operators, destructors) are not
   * part of the interface.
   *
   * Template instantiations are skipped: they share the location of their class
   * template, so reporting them would duplicate the alert (with a different,
   * instantiation-dependent count) and CodeQL keeps only one result per location.
   */
  bindingset[limit]
  predicate publicInterfaces(Class c, int interfaces, int limit) {
    c.fromSource() and
    not c instanceof ClassTemplateInstantiation and
    interfaces =
      count(MemberFunction m |
        m = c.getAMemberFunction() and
        not m.isPrivate() and
        not m.isProtected() and
        not m.isCompilerGenerated()
      ) and
    interfaces > limit
  }
}
