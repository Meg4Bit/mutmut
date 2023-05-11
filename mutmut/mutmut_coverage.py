import io
import pytest
import git
import difflib
import subprocess

from pathlib import Path
from contextlib import redirect_stdout
from coverage import CoverageData, Coverage


def current_commit():
    repo = git.Repo(".")
    return repo.head.commit.hexsha


def parse_diff_header(str):
    a, b = str.strip('@ ').split(' ')
    a = list(map(int, a[1:].split(',')))
    b = list(map(int, b[1:].split(',')))
    return a[0], b[0]


def program_files(paths_to_mutate, paths_to_exclude):
    files = []
    from mutmut import python_source_files
    for path in paths_to_mutate:
        for filename in python_source_files(path, [], paths_to_exclude):
            files.append(filename)
    return files


def find_difference(changes_dict):
    from mutmut.cache import commit_hash
    repo = git.Repo(".")
    old_commit = repo.commit(commit_hash())
    for git_diff in old_commit.diff("HEAD").iter_change_type("M"):
        if git_diff.a_path.endswith('.py'):
            a_str_list = git_diff.a_blob.data_stream.read().decode('utf-8').split('\n')
            b_str_list = git_diff.b_blob.data_stream.read().decode('utf-8').split('\n')
            str_diff = difflib.unified_diff(a_str_list, b_str_list, n=0, lineterm=' ')
            diff_lines = list(str_diff)
            a_line, b_line = 0, 0
            path = git_diff.a_blob.abspath
            changes_dict[path] = {"-": [], "+": []}
            for line in diff_lines[2:]:
                if line[0] == '@':
                    a_line, b_line = parse_diff_header(line)
                if line[0] == '-':
                    changes_dict[path]['-'].append(a_line)
                    a_line += 1
                if line[0] == '+':
                    changes_dict[path]['+'].append(b_line)
                    b_line += 1


def covered_files_lists(prev_covered_files, new_covered_files):
    list_prev_covered_files = prev_covered_files.keys()
    list_new_covered_files = new_covered_files.keys()
    list_len_diff = len(list_prev_covered_files) - len(list_new_covered_files)
    list_intersection = [item for item in list_prev_covered_files if item in list_new_covered_files]
    list_prev_covered_files = list_intersection + [item for item in list_prev_covered_files if item not in list_intersection]
    list_new_covered_files = list_intersection + [item for item in list_new_covered_files if item not in list_intersection]
    if list_len_diff > 0:
        list_new_covered_files += [None] * list_len_diff
    else:
        list_prev_covered_files += [None] * abs(list_len_diff)
    return list_prev_covered_files, list_new_covered_files


def tests_with_changes(changed_lines, covered_file, changed_tests):
    for line in changed_lines:
        if line in covered_file:
            changed_tests += [test for test in covered_file[line] if test not in changed_tests]


def find_changed_tests(dict_prev_covered_files, dict_new_covered_files, changes_dict):
    changed_tests = []
    list_prev_covered_files, list_new_covered_files = covered_files_lists(dict_prev_covered_files, dict_new_covered_files)
    for prev_covered_file, new_covered_file in zip(list_prev_covered_files, list_new_covered_files):
        if prev_covered_file and prev_covered_file in changes_dict.keys():    # Tests which coverage affected by changes
            tests_with_changes(changes_dict[prev_covered_file]['-'], dict_prev_covered_files[prev_covered_file], changed_tests)
        if new_covered_file and new_covered_file in changes_dict.keys():    # Tests which coverage affected by changes
            tests_with_changes(changes_dict[new_covered_file]['+'], dict_new_covered_files[new_covered_file], changed_tests)
        if prev_covered_file == new_covered_file and prev_covered_file in changes_dict.keys():    # Tests with changed coverage
            prev_lines = [line for line in dict_prev_covered_files[prev_covered_file].keys() if line not in changes_dict[prev_covered_file]['-']]
            prev_lines.sort()
            new_lines = [line for line in dict_new_covered_files[new_covered_file].keys() if line not in changes_dict[new_covered_file]['+']]
            new_lines.sort()
            for prev, new in zip(prev_lines, new_lines):
                changed_coverage = list(set(dict_prev_covered_files[prev_covered_file][prev]).symmetric_difference(set(dict_new_covered_files[new_covered_file][new])))
                changed_tests += [test for test in changed_coverage if test not in changed_tests]
    return [item for item in changed_tests if item != '']


def find_changed_mutants(prev_covered_files, tests_list):
    from mutmut.cache import tested_mutants

    changed_mutants = []
    mutants = tested_mutants()
    for mutant in mutants:
        if mutant.filename in prev_covered_files and mutant.line_number + 1 in prev_covered_files[mutant.filename] and \
            set(tests_list) & set(prev_covered_files[mutant.filename][mutant.line_number + 1]):
            changed_mutants.append(mutant)
    return changed_mutants


def modified_coverage(new_covered_files):
    changes_dict = {}
    modified_coverage = {}
    cov_data = CoverageData(".coverage_old")
    cov_data.read()
    prev_covered_files = {filepath: cov_data.contexts_by_lineno(filepath) for filepath in cov_data.measured_files()}
    find_difference(changes_dict)
    tests_list = find_changed_tests(prev_covered_files, new_covered_files, changes_dict)
    for file in new_covered_files:
        modified_coverage[file] = {line: new_covered_files[file][line] \
                                    for line in new_covered_files[file] \
                                    if any(test in new_covered_files[file][line] for test in tests_list)}
    changed_mutants = find_changed_mutants(prev_covered_files, tests_list)
    return modified_coverage, changed_mutants

    
def measure_coverage(argument, paths_to_mutate, tests_dirs):
    """Find all test files located under the 'tests' directory and calculate coverage"""
    # files = program_files(paths_to_mutate, paths_to_exclude)
    command = ["pytest"] + tests_dirs + ['--cov=' + ','.join(paths_to_mutate), '--cov-context=test', '-q', '--no-summary', '--no-header']
    result = subprocess.check_output(command)
    # with io.StringIO() as buf, redirect_stdout(buf):
    #     pytest.main(args=tests_dirs + ['--cov=' + ','.join(paths_to_mutate), '--cov-context=test', '-q', '--no-summary', '--no-header'])
    cov_data = CoverageData()
    cov_data.read()
    new_covered_files = {filepath: cov_data.contexts_by_lineno(filepath) for filepath in cov_data.measured_files()}
    return new_covered_files


    # if argument and os.path.exists(argument):
    #     cov_path = argument
    # else:
    #     cov_path = []
    #     for path in paths_to_mutate:
    #         for filename in python_source_files(path, tests_dirs, paths_to_exclude):
    #             if not (os.path.basename(filename).startswith('test_') or filename.endswith('__tests.py')):
    #                 cov_path.append(filename)
    # pytest.main(args=tests_dirs + ['--cov=' + ','.join(paths_to_mutate), '--cov-context=test', '-q', '--no-summary', '--no-header'])
    # cov_data = CoverageData()
    # cov_data.read()
    # print(cov_data.measured_files())

def changed_sample(coverage_to_mutate, mutations_by_file):
    changed_coverage_mutants = []
    for file in mutations_by_file:
        if file in coverage_to_mutate:
            for mutant in mutations_by_file[file]:
                if mutant.line_number + 1 in coverage_to_mutate[file]:
                    changed_coverage_mutants.append(mutant)
    return changed_coverage_mutants
    