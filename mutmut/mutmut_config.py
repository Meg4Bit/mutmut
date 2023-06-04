import os
import re


def pre_mutation(context):
    """Use coverage to reduce test suite"""
    if not context.config.coverage_data:
        # mutmut was run without ``--use-coverage``
        return
    fname = os.path.abspath(context.filename)
    contexts_for_file = context.config.coverage_data.get(fname, {})
    contexts_for_line = contexts_for_file.get(context.current_line_index, [])
    test_names = [
        '\'' + re.sub(r'([\[](.*)[\]])?\|.*', lambda x: "[" + x.group(2) + "]" if x.group(1) else x.group(1), ctx).replace("\'", "\'\"\'\"\'") + '\''
        for ctx in contexts_for_line
        if ctx  # skip empty strings
    ]
    if "-x --assert=plain" not in context.config.test_command:
        context.config.test_command += " -x --assert=plain"
    if not test_names:
        return
    context.config.test_command += f' {" ".join(test_names)}'
    