import git
import difflib
import subprocess
import copy

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
    for change_type in ['A', 'D', 'M', 'R']:
        for git_diff in old_commit.diff("HEAD").iter_change_type(change_type):
            if git_diff.a_path.endswith('.py'):
                a_str_list = []
                b_str_list = []
                if git_diff.a_blob:
                    a_str_list = git_diff.a_blob.data_stream.read().decode('utf-8').split('\n')
                if git_diff.b_blob:
                    b_str_list = git_diff.b_blob.data_stream.read().decode('utf-8').split('\n')
                str_diff = difflib.unified_diff(a_str_list, b_str_list, n=0, lineterm=' ')
                diff_lines = list(str_diff)
                a_line, b_line = 0, 0
                if change_type == 'R':
                    path = git_diff.a_blob.abspath + ":" + git_diff.b_blob.abspath 
                else:
                    path = git_diff.a_blob.abspath if git_diff.a_blob else git_diff.b_blob.abspath
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


def equal_files_changes(prev_covered_file, new_covered_file, file, changes_dict, changed_tests):
    if file in changes_dict:
        prev_lines = [line for line in prev_covered_file if line not in changes_dict[file]['-']]
        new_lines = [line for line in new_covered_file if line not in changes_dict[file]['+']]
    else:
        prev_lines = [line for line in prev_covered_file]
        new_lines = [line for line in new_covered_file]
    prev_lines.sort()
    new_lines.sort()
    if not new_lines:
        for prev in prev_lines:
            changed_tests += [test for test in prev_covered_file[prev] \
                                if test not in changed_tests]
    for prev, new in zip(prev_lines, new_lines):
        changed_coverage = list(set(prev_covered_file[prev]).symmetric_difference(set(new_covered_file[new])))
        changed_tests += [test for test in changed_coverage if test not in changed_tests]


def find_changed_tests(dict_prev_covered_files, dict_new_covered_files, changes_dict):
    changed_tests = []
    list_prev_covered_files, list_new_covered_files = covered_files_lists(dict_prev_covered_files, dict_new_covered_files)
    for prev_covered_file, new_covered_file in zip(list_prev_covered_files, list_new_covered_files):
        if prev_covered_file and prev_covered_file in changes_dict.keys():    # Tests which coverage affected by changes
            tests_with_changes(changes_dict[prev_covered_file]['-'], dict_prev_covered_files[prev_covered_file], changed_tests)
        if new_covered_file and new_covered_file in changes_dict.keys():    # Tests which coverage affected by changes
            tests_with_changes(changes_dict[new_covered_file]['+'], dict_new_covered_files[new_covered_file], changed_tests)
        if prev_covered_file == new_covered_file:    # Tests with changed coverage
            equal_files_changes(dict_prev_covered_files[prev_covered_file], dict_new_covered_files[new_covered_file], \
                                prev_covered_file, changes_dict, changed_tests)
    for file in changes_dict:
        if ':' in file:
            prev_covered_file, new_covered_file = file.split(':')
            if prev_covered_file in dict_prev_covered_files:
                tests_with_changes(changes_dict[file]['-'], dict_prev_covered_files[prev_covered_file], changed_tests)
            if new_covered_file in dict_new_covered_files:
                tests_with_changes(changes_dict[file]['+'], dict_new_covered_files[new_covered_file], changed_tests)
            if prev_covered_file in dict_prev_covered_files and new_covered_file in dict_new_covered_files:
                equal_files_changes(dict_prev_covered_files[prev_covered_file], dict_new_covered_files[new_covered_file], \
                                    file, changes_dict, changed_tests)
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

    
def measure_coverage(argument, paths_to_mutate, tests_dirs, test_command):
    """Find all test files located under the 'tests' directory and calculate coverage"""
    # files = program_files(paths_to_mutate, paths_to_exclude)
    command = test_command.split(' ') + ['--cov=' + ','.join(paths_to_mutate), '--cov-context=test', '-q']
    result = subprocess.check_output(command)  #TODO add paths to coverage
    cov_data = CoverageData()
    cov_data.read()
    new_covered_files = {filepath: cov_data.contexts_by_lineno(filepath) for filepath in cov_data.measured_files()}
    return new_covered_files


def changed_sample(coverage_to_mutate, mutations_by_file):
    changed_coverage_mutants = []
    for file in mutations_by_file:
        if file in coverage_to_mutate:
            for mutant in mutations_by_file[file]:
                if mutant.line_number + 1 in coverage_to_mutate[file]:
                    changed_coverage_mutants.append(mutant)
    return changed_coverage_mutants


def empty_coverage_sample(coverage_data, mutations_by_file, t_mutants):
    empty_coverage = {}
    for file in coverage_data:
        empty_coverage[file] = {line: coverage_data[file][line] for line in coverage_data[file] if coverage_data[file][line] == ['']}
    return [elem for elem in changed_sample(empty_coverage, mutations_by_file) if elem not in t_mutants]


def update_mutants(mutants, hash_of_tests, commit):
    from mutmut.cache import update_mutants_test_hash, create_mutants, renamed_line_number, \
                                update_mutant_status, cached_mutation_status, update_line_numbers
    update_mutants_test_hash(mutants, hash_of_tests)
    old_mutants = []
    new_mutants = []
    repo = git.Repo(".")
    old_commit = repo.commit(commit)
    for git_diff in old_commit.diff("HEAD").iter_change_type('R'):
        for mutant in mutants:
            if mutant.filename == git_diff.a_blob.abspath:
                new_mutant = copy.deepcopy(mutant)
                new_mutant.filename = git_diff.b_blob.abspath
                update_line_numbers(new_mutant.filename)
                new_mutant.line_number = renamed_line_number(mutant.line_number, git_diff.a_blob.abspath, git_diff.b_blob.abspath)
                if new_mutant.line_number is not None:
                    new_mutants.append(new_mutant)
                    old_mutants.append(mutant)
                else:
                    update_mutant_status(mutant.filename, mutant, 'untested', '')
    create_mutants(new_mutants)
    for new_mutant, old_mutant in zip(new_mutants, old_mutants):
        status = cached_mutation_status(old_mutant.filename, old_mutant, hash_of_tests)
        update_mutant_status(new_mutant.filename, new_mutant, status, hash_of_tests)
        update_mutant_status(old_mutant.filename, old_mutant, 'untested', '')


def empty_coverage_changed_mutants(mutations_by_file, coverage_data):
    changes_dict = {}
    changed_mutants = []
    find_difference(changes_dict)
    empty_coverage_mutations = empty_coverage_sample(coverage_data, mutations_by_file, [])
    for mutation_id in empty_coverage_mutations:
        if mutation_id.filename in changes_dict and \
            mutation_id.line_number + 1 in changes_dict[mutation_id.filename]['+']:
            changed_mutants.append(mutation_id)
    return changed_mutants
