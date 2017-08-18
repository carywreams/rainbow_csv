#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

import sys
import os
import argparse
import random
import unittest
import re
import tempfile
import time
import importlib
import codecs
import io


#This module must be both python2 and python3 compatible
#TODO add other languages for functions: java, node js, cpp, perl

#TODO if query contains non-ascii symbols, read input files in utf-8 encoding (in other cases use latin-1 as usual)


default_csv_encoding = 'latin-1'

PY3 = sys.version_info[0] == 3

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def dynamic_import(module_name):
    try:
        importlib.invalidate_caches()
    except AttributeError:
        pass
    return importlib.import_module(module_name)


def get_encoded_stdin(encoding_name):
    if PY3:
        return io.TextIOWrapper(sys.stdin.buffer, encoding=encoding_name)
    else:
        return codecs.getreader(encoding_name)(sys.stdin)

def get_encoded_stdout(encoding_name):
    if PY3:
        return io.TextIOWrapper(sys.stdout.buffer, encoding=encoding_name)
    else:
        return codecs.getwriter(encoding_name)(sys.stdout)


column_var_regex = re.compile(r'^a([1-9][0-9]*)$')
bcolumn_var_regex = re.compile(r'^b([1-9][0-9]*)$')


def replace_rbql_var(text):
    mtobj = column_var_regex.match(text)
    if mtobj is not None:
        column_number = int(mtobj.group(1))
        return 'fields[{}]'.format(column_number - 1)
    mtobj = bcolumn_var_regex.match(text)
    if mtobj is not None:
        column_number = int(mtobj.group(1))
        return 'bfields[{}]'.format(column_number - 1)
    return None


class RBParsingError(Exception):
    pass

def xrange6(x):
    if PY3:
        return range(x)
    return xrange(x)


def is_digit(c):
    v = ord(c)
    return v >= ord('0') and v <= ord('9')


def is_boundary(c):
    if c == '_':
        return False
    v = ord(c)
    if v >= ord('a') and v <= ord('z'):
        return False
    if v >= ord('A') and v <= ord('Z'):
        return False
    if v >= ord('0') and v <= ord('9'):
        return False
    return True


def is_escaped_quote(cline, i):
    if i == 0:
        return False
    if i == 1 and cline[i - 1] == '\\':
        return True
    #Don't fix for raw string literals: double backslash before quote is not allowed there
    if cline[i - 1] == '\\' and cline[i - 2] != '\\':
        return True
    return False


def strip_comments(cline):
    cline = cline.rstrip()
    cline = cline.replace('\t', ' ')
    cur_quote_mark = None
    for i in xrange6(len(cline)):
        c = cline[i]
        if cur_quote_mark is None and c == '#':
            return cline[:i].rstrip()
        if cur_quote_mark is None and (c == "'" or c == '"'):
            cur_quote_mark = c
            continue
        if cur_quote_mark is not None and c == cur_quote_mark and not is_escaped_quote(cline, i):
            cur_quote_mark = None
    return cline


class TokenType:
    RAW = 1
    STRING_LITERAL = 2
    WHITESPACE = 3
    ALPHANUM_RAW = 4
    SYMBOL_RAW = 5

class Token:
    def __init__(self, ttype, content):
        self.ttype = ttype
        self.content = content

    def __str__(self):
        return '{}\t{}'.join(self.ttype, self.content)


def tokenize_string_literals(lines):
    result = list()
    for cline in lines:
        cur_quote_mark = None
        k = 0
        i = 0
        while i < len(cline):
            c = cline[i]
            if cur_quote_mark is None and (c == "'" or c == '"'):
                cur_quote_mark = c
                result.append(Token(TokenType.RAW, cline[k:i]))
                k = i
            elif cur_quote_mark is not None and c == cur_quote_mark and not is_escaped_quote(cline, i):
                cur_quote_mark = None
                result.append(Token(TokenType.STRING_LITERAL, cline[k:i + 1]))
                k = i + 1
            i += 1
        if k < i:
            result.append(Token(TokenType.RAW, cline[k:i]))
        result.append(Token(TokenType.WHITESPACE, ' '))
    return result


def tokenize_terms(tokens):
    result = list()
    for token in tokens:
        if token.ttype != TokenType.RAW:
            result.append(token)
            continue
        content = token.content

        i = 0
        k = 0
        in_alphanumeric = False 
        while i < len(content):
            c = content[i]
            if c == ' ' or is_boundary(c):
                if k < i:
                    assert in_alphanumeric
                    result.append(Token(TokenType.ALPHANUM_RAW, content[k:i]))
                k = i + 1
                in_alphanumeric = False
                if c == ' ':
                    result.append(Token(TokenType.WHITESPACE, ' '))
                else:
                    result.append(Token(TokenType.SYMBOL_RAW, c))
            elif not in_alphanumeric:
                in_alphanumeric = True
                k = i
            i += 1
        if k < i:
            assert in_alphanumeric
            result.append(Token(TokenType.ALPHANUM_RAW, content[k:i]))
    return result


def remove_consecutive_whitespaces(tokens):
    result = list()
    for i in xrange6(len(tokens)):
        if (tokens[i].ttype != TokenType.WHITESPACE) or (i == 0) or (tokens[i - 1].ttype != TokenType.WHITESPACE):
            result.append(tokens[i])
    if len(result) and result[0].ttype == TokenType.WHITESPACE:
        result = result[1:]
    if len(result) and result[-1].ttype == TokenType.WHITESPACE:
        result = result[:-1]
    return result


def replace_column_vars(tokens):
    for i in xrange6(len(tokens)):
        if tokens[i].ttype == TokenType.STRING_LITERAL:
            continue
        replaced_py_var = replace_rbql_var(tokens[i].content)
        if replaced_py_var is not None:
            tokens[i].content = replaced_py_var
    return tokens

def replace_star_vars(tokens):
    for i in xrange6(len(tokens)):
        if tokens[i].ttype == TokenType.STRING_LITERAL:
            continue
        if tokens[i].content != '*':
            continue
        j = i - 1
        if j >= 0 and tokens[j].content == ' ':
            j -= 1
        if j >= 0:
            assert tokens[j].content != ' '
            if not tokens[j].content.endswith(','):
                continue
        j = i + 1
        if j < len(tokens) and tokens[j].content == ' ':
            j += 1
        if j < len(tokens):
            assert tokens[j].content != ' '
            if not tokens[j].content.startswith(','):
                continue
        tokens[i].content = 'star_line'
    return tokens


def join_tokens(tokens):
    return ''.join([t.content for t in tokens]).strip()


class Pattern:
    def __init__(self, text):
        self.text = text
        self.tokens = list()
        fields = text.split(' ')
        for i in xrange6(len(fields)):
            self.tokens.append(Token(TokenType.ALPHANUM_RAW, fields[i]))
            if i + 1 < len(fields):
                self.tokens.append(Token(TokenType.WHITESPACE, ' '))
        self.size = len(self.tokens)


def consume_action(patterns, tokens, idx):
    if tokens[idx].ttype == TokenType.STRING_LITERAL:
        return (None, idx + 1)
    for pattern in patterns:
        if idx + pattern.size >= len(tokens):
            continue
        input_slice = tokens[idx:idx + pattern.size]
        if [t.content.upper() for t in input_slice] != [t.content for t in pattern.tokens]:
            continue
        if [t.ttype for t in input_slice] != [t.ttype for t in pattern.tokens]:
            continue
        return (pattern.text, idx + pattern.size)
    return (None, idx + 1)


def strip_tokens(tokens):
    while len(tokens) and tokens[0].content == ' ':
        tokens = tokens[1:]
    while len(tokens) and tokens[-1].content == ' ':
        tokens = tokens[:-1]
    return tokens


def separate_actions(tokens):
    result = dict()
    prev_action = None
    k = 0
    i = 0
    patterns = ['STRICT LEFT JOIN', 'LEFT JOIN', 'INNER JOIN', 'SELECT DISTINCT', 'SELECT', 'ORDER BY', 'WHERE']
    patterns = sorted(patterns, key=len, reverse=True)
    patterns = [Pattern(p) for p in patterns]
    while i < len(tokens):
        action, i_next = consume_action(patterns, tokens, i)
        if action is None:
            i = i_next
            continue
        if prev_action is not None:
            result[prev_action] = strip_tokens(tokens[k:i])
        if action in result:
            raise RBParsingError('More than one "{}" statements found'.format(action))
        prev_action = action
        i = i_next
        k = i
    if prev_action is not None:
        result[prev_action] = strip_tokens(tokens[k:i])
    return result




py_script_body = r'''#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys
import os
import random #for random sort
import datetime #for date manipulations
import re #for regexes
import codecs

{import_expression}


PY3 = sys.version_info[0] == 3

def str6(obj):
    if PY3 and isinstance(obj, str):
        return obj
    if not PY3 and isinstance(obj, basestring):
        return obj
    return str(obj)

DLM = '{dlm}'

def rows(f, chunksize=1024, sep='\n'):
    incomplete_row = None
    while True:
        chunk = f.read(chunksize)
        if not chunk:
            if incomplete_row is not None and len(incomplete_row):
                yield incomplete_row
            return
        while True:
            i = chunk.find(sep)
            if i == -1:
                break
            if incomplete_row is not None:
                yield incomplete_row + chunk[:i]
                incomplete_row = None
            else:
                yield chunk[:i]
            chunk = chunk[i+1:]
        if incomplete_row is not None:
            incomplete_row += chunk
        else:
            incomplete_row = chunk


class BadFieldError(Exception):
    def __init__(self, bad_idx):
        self.bad_idx = bad_idx

class RbqlRuntimeError(Exception):
    pass


class rbql_list(list):
    def __getitem__(self, idx):
        try:
            v = super(rbql_list, self).__getitem__(idx)
        except IndexError as e:
            raise BadFieldError(idx)
        return v


class Flike:
    def __init__(self):
        self._cache = dict()

    def _like_to_regex(self, pattern):
        p = 0
        i = 0
        converted = ''
        while i < len(pattern):
            if pattern[i] in ['_', '%']:
                converted += re.escape(pattern[p:i])
                p = i + 1
                if pattern[i] == '_':
                    converted += '.'
                else:
                    converted += '.*'
            i += 1
        converted += re.escape(pattern[p:i])
        return '^' + converted + '$'

    def __call__(self, text, pattern):
        if pattern not in self._cache:
            rgx = self._like_to_regex(pattern)
            self._cache[pattern] = re.compile(rgx)
        return self._cache[pattern].match(text) is not None

flike = Flike()


class SimpleWriter:
    def __init__(self, dst):
        self.dst = dst

    def write(self, record):
        self.dst.write(record)
        self.dst.write('\n')


class UniqWriter:
    def __init__(self, dst):
        self.dst = dst
        self.seen = set()

    def write(self, record):
        if record in self.seen:
            return
        self.seen.add(record)
        self.dst.write(record)
        self.dst.write('\n')


def read_join_table(join_table_path):
    fields_max_len = 0
    if not os.path.isfile(join_table_path):
        raise RbqlRuntimeError('Table B: ' + join_table_path + ' is not accessible')
    result = dict()
    with codecs.open(join_table_path, encoding='{join_encoding}') as src_text:
        for il, line in enumerate(rows(src_text), 1):
            line = line.rstrip('\r\n')
            bfields = rbql_list(line.split(DLM))
            fields_max_len = max(fields_max_len, len(bfields))
            try:
                key = {rhs_join_var}
            except BadFieldError as e:
                bad_idx = e.bad_idx
                raise RbqlRuntimeError('No "b' + str(bad_idx + 1) + '" column at line: ' + str(il) + ' in "B" table')
            if key in result:
                raise RbqlRuntimeError('Join column must be unique in right-hand-side "B" table')
            result[key] = bfields
    return (result, fields_max_len)


def none_joiner(path):
    return None


class InnerJoiner:
    def __init__(self, join_table_path):
        self.join_data, self.fields_max_len = read_join_table(join_table_path)
    def get(self, lhs_key):
        return self.join_data.get(lhs_key, None)


class LeftJoiner:
    def __init__(self, join_table_path):
        self.join_data, self.fields_max_len = read_join_table(join_table_path)
    def get(self, lhs_key):
        return self.join_data.get(lhs_key, [None] * self.fields_max_len)


class StrictLeftJoiner:
    def __init__(self, join_table_path):
        self.join_data, self.fields_max_len = read_join_table(join_table_path)
    def get(self, lhs_key):
        result = self.join_data.get(lhs_key, None)
        if result is None:
            raise RbqlRuntimeError('In "STRICT LEFT JOIN" mode all A table keys must be present in table B. Key "' + lhs_key + '" was not found')
        return result


def main():
    rb_transform(sys.stdin, sys.stdout)


def rb_transform(source, destination):
    unsorted_entries = list()
    writer = {writer_type}(destination)
    joiner = {joiner_type}('{rhs_table_path}')
    for NR, line in enumerate(rows(source), 1):
        lnum = NR #TODO remove, backcompatibility
        line = line.rstrip('\r\n')
        star_line = line
        fields = rbql_list(line.split(DLM))
        NF = len(fields)
        flen = NF #TODO remove, backcompatibility
        bfields = None
        try:
            if joiner is not None:
                bfields = joiner.get({lhs_join_var})
                if bfields is None:
                    continue
                star_line = DLM.join([line] + [str6(f) for f in bfields])
            if not ({where_expression}):
                continue
            out_fields = [{select_expression}]
            if {sort_flag}:
                sort_key_value = ({sort_key_expression})
                unsorted_entries.append((sort_key_value, DLM.join([str6(f) for f in out_fields])))
            else:
                writer.write(DLM.join([str6(f) for f in out_fields]))
        except BadFieldError as e:
            bad_idx = e.bad_idx
            raise RbqlRuntimeError('No "a' + str(bad_idx + 1) + '" column at line: ' + str(NR))
        except Exception as e:
            raise RbqlRuntimeError('Error at line: ' + str(NR) + ', Details: ' + str(e))
    if len(unsorted_entries):
        unsorted_entries = sorted(unsorted_entries, reverse = {reverse_flag})
        for e in unsorted_entries:
            writer.write(e[1])


if __name__ == '__main__':
    main()

'''


def vim_sanitize(obj):
    return str(obj).replace("'", '"')

def set_vim_variable(vim, var_name, value):
    str_value = str(value).replace("'", '"')
    vim.command("let {} = '{}'".format(var_name, str_value))

def normalize_delim(delim):
    if delim == '\t':
        return r'\t'
    return delim


def parse_join_expression(tokens):
    syntax_err_msg = 'Incorrect join syntax. Must be: "<JOIN> /path/to/B/table on a<i> == b<j>"'
    tokens = join_tokens(tokens).split(' ')
    if len(tokens) != 5 or tokens[1].upper() != 'ON' or tokens[3] != '==':
        raise RBParsingError(syntax_err_msg)
    if column_var_regex.match(tokens[4]) is not None:
        tokens[2], tokens[4] = tokens[4], tokens[2]
    if column_var_regex.match(tokens[2]) is None or bcolumn_var_regex.match(tokens[4]) is None:
        raise RBParsingError(syntax_err_msg)
    return (tokens[0], replace_rbql_var(tokens[2]), replace_rbql_var(tokens[4]))


def parse_to_py(rbql_lines, py_dst, delim, join_csv_encoding=default_csv_encoding, import_modules=None):
    if not py_dst.endswith('.py'):
        raise RBParsingError('python module file must have ".py" extension')

    for il in xrange6(len(rbql_lines)):
        cline = rbql_lines[il]
        if cline.find("'''") != -1 or cline.find('"""') != -1: #TODO improve parsing to allow multiline strings/comments
            raise RBParsingError('In line {}. Multiline python comments and doc strings are not allowed in rbql'.format(il + 1))
        rbql_lines[il] = strip_comments(cline)

    rbql_lines = [l for l in rbql_lines if len(l)]

    tokens = tokenize_string_literals(rbql_lines)
    tokens = tokenize_terms(tokens)
    tokens = remove_consecutive_whitespaces(tokens)
    rb_actions = separate_actions(tokens)

    select_op = None
    writer_name = None
    select_ops = {'SELECT': 'SimpleWriter', 'SELECT DISTINCT': 'UniqWriter'}
    for k, v in select_ops.items():
        if k in rb_actions:
            select_op = k
            writer_name = v

    if select_op is None:
        raise RBParsingError('"SELECT" statement not found')

    joiner_name = 'none_joiner'
    join_op = None
    rhs_table_path = None
    lhs_join_var = None
    rhs_join_var = None
    join_ops = {'INNER JOIN': 'InnerJoiner', 'LEFT JOIN': 'LeftJoiner', 'STRICT LEFT JOIN': 'StrictLeftJoiner'}
    for k, v in join_ops.items():
        if k in rb_actions:
            join_op = k
            joiner_name = v

    if join_op is not None:
        rhs_table_path, lhs_join_var, rhs_join_var = parse_join_expression(rb_actions[join_op])

    py_meta_params = dict()
    import_expression = ''
    if import_modules is not None:
        for mdl in import_modules:
            import_expression += 'import {}\n'.format(mdl)
    py_meta_params['import_expression'] = import_expression
    py_meta_params['dlm'] = normalize_delim(delim)
    py_meta_params['join_encoding'] = join_csv_encoding
    py_meta_params['rhs_join_var'] = rhs_join_var
    py_meta_params['writer_type'] = writer_name
    py_meta_params['joiner_type'] = joiner_name
    py_meta_params['rhs_table_path'] = rhs_table_path
    py_meta_params['lhs_join_var'] = lhs_join_var
    py_meta_params['where_expression'] = 'True'
    if 'WHERE' in rb_actions:
        py_meta_params['where_expression'] = join_tokens(replace_column_vars(rb_actions['WHERE']))
    select_tokens = replace_column_vars(rb_actions[select_op])
    select_tokens = replace_star_vars(select_tokens)
    if not len(select_tokens):
        raise RBParsingError('"SELECT" expression is empty')
    select_expression= join_tokens(select_tokens)
    py_meta_params['select_expression'] = select_expression

    py_meta_params['sort_flag'] = 'False'
    py_meta_params['reverse_flag'] = 'False'
    py_meta_params['sort_key_expression'] = 'None'
    if 'ORDER BY' in rb_actions:
        py_meta_params['sort_flag'] = 'True'
        order_expression = join_tokens(replace_column_vars(rb_actions['ORDER BY']))
        direction_marker = ' DESC'
        if order_expression.upper().endswith(direction_marker):
            order_expression = order_expression[:-len(direction_marker)].rstrip()
            py_meta_params['reverse_flag'] = 'True'
        direction_marker = ' ASC'
        if order_expression.upper().endswith(direction_marker):
            order_expression = order_expression[:-len(direction_marker)].rstrip()
        py_meta_params['sort_key_expression'] = order_expression

    with codecs.open(py_dst, 'w', encoding='utf-8') as dst:
        dst.write(py_script_body.format(**py_meta_params))




def vim_execute(src_table_path, rb_script_path, py_script_path, dst_table_path, delim, csv_encoding=default_csv_encoding):
    if os.path.exists(py_script_path):
        os.remove(py_script_path)
    import vim
    try:
        src_lines = codecs.open(rb_script_path, encoding='utf-8').readlines()
        parse_to_py(src_lines, py_script_path, delim, csv_encoding)
    except RBParsingError as e:
        set_vim_variable(vim, 'query_status', 'Parsing Error')
        set_vim_variable(vim, 'report', e)
        return

    module_name = os.path.basename(py_script_path)
    assert module_name.endswith('.py')
    module_name = module_name[:-3]
    module_dir = os.path.dirname(py_script_path)
    sys.path.insert(0, module_dir)
    try:
        rbconvert = dynamic_import(module_name)

        src = codecs.open(src_table_path, encoding=csv_encoding)
        with codecs.open(dst_table_path, 'w', encoding=csv_encoding) as dst:
            rbconvert.rb_transform(src, dst)
        src.close()
    except Exception as e:
        error_msg = 'Error: Unable to use generated python module.\n'
        error_msg += 'Original python exception:\n{}\n'.format(str(e))
        set_vim_variable(vim, 'query_status', 'Execution Error')
        set_vim_variable(vim, 'report', error_msg)
        tmp_dir = tempfile.gettempdir()
        import traceback
        with open(os.path.join(tmp_dir, 'last_exception'), 'w') as exc_dst:
            traceback.print_exc(file=exc_dst)
        return
    set_vim_variable(vim, 'query_status', 'OK')



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--delim', help='Delimiter', default=r'\t')
    parser.add_argument('--query', help='Query string in rbql')
    parser.add_argument('--query_file', metavar='FILE', help='Read rbql query from FILE')
    parser.add_argument('--input_table_path', metavar='FILE', help='Read csv table from FILE instead of stdin')
    parser.add_argument('--output_table_path', metavar='FILE', help='Write output table to FILE instead of stdout')
    parser.add_argument('--convert_only', action='store_true', help='Only generate python script do not run query on csv table')
    parser.add_argument('--csv_encoding', help='Manually set csv table encoding', default=default_csv_encoding, choices=['latin-1', 'utf-8'])
    parser.add_argument('-I', dest='libs', action='append', help='Import module to use in the result conversion script. Can be used multiple times')
    args = parser.parse_args()

    delim = args.delim
    query = args.query
    query_path = args.query_file
    convert_only = args.convert_only
    input_path = args.input_table_path
    output_path = args.output_table_path
    import_modules = args.libs
    csv_encoding = args.csv_encoding

    rbql_lines = None
    if query is None and query_path is None:
        eprint('Error: provide either "--query" or "--query_path" option')
        sys.exit(1)
    if query is not None and query_path is not None:
        eprint('Error: unable to use both "--query" and "--query_path" options')
        sys.exit(1)
    if query_path is not None:
        assert query is None
        rbql_lines = codecs.open(query_path, encoding='utf-8').readlines()
    else:
        assert query_path is None
        rbql_lines = [query]

    tmp_dir = tempfile.gettempdir()

    module_name = 'rbconvert_{}'.format(time.time()).replace('.', '_')
    module_filename = '{}.py'.format(module_name)

    tmp_path = os.path.join(tmp_dir, module_filename)

    try:
        parse_to_py(rbql_lines, tmp_path, delim, csv_encoding, import_modules)
    except RBParsingError as e:
        eprint('RBQL Parsing Error: \t{}'.format(e))
        sys.exit(1)
    if not os.path.isfile(tmp_path) or not os.access(tmp_path, os.R_OK):
        eprint('Error: Unable to find generated python module at {}.'.format(tmp_path))
        sys.exit(1)
    sys.path.insert(0, tmp_dir)
    try:
        rbconvert = dynamic_import(module_name)
        src = None
        if input_path:
            src = codecs.open(input_path, encoding=csv_encoding)
        else:
            src = get_encoded_stdin(csv_encoding)
        if output_path:
            with codecs.open(output_path, 'w', encoding=csv_encoding) as dst:
                rbconvert.rb_transform(src, dst)
        else:
            dst = get_encoded_stdout(csv_encoding)
            rbconvert.rb_transform(src, dst)
    except Exception as e:
        error_msg = 'Error: Unable to use generated python module.\n'
        error_msg += 'Location of the generated module: {}\n\n'.format(tmp_path)
        error_msg += 'Original python exception:\n{}\n'.format(str(e))
        eprint(error_msg)
        sys.exit(1)



def table_to_string(array2d, delim):
    result = '\n'.join([delim.join(ln) for ln in array2d])
    if len(array2d):
        result += '\n'
    return result


def table_to_file(array2d, dst_path, delim='\t'):
    with open(dst_path, 'w') as f:
        for row in array2d:
            f.write(delim.join(row))
            f.write('\n')


def table_to_stream(array2d, delim):
    return io.StringIO(table_to_string(array2d, delim))


def run_file_query_test(query, input_path, testname, import_modules=None, csv_encoding=default_csv_encoding, delim='\t'):
    tmp_dir = tempfile.gettempdir()
    if not len(sys.path) or sys.path[0] != tmp_dir:
        sys.path.insert(0, tmp_dir)
    module_name = '{}{}_{}_{}'.format(rainbow_ut_prefix, time.time(), testname, random.randint(1, 100000000)).replace('.', '_')
    module_filename = '{}.py'.format(module_name)
    tmp_path = os.path.join(tmp_dir, module_filename)
    dst_table_filename = '{}.tsv'.format(module_name)
    output_path = os.path.join(tmp_dir, dst_table_filename)
    parse_to_py([query], tmp_path, delim, csv_encoding, import_modules)
    rbconvert = dynamic_import(module_name)
    with codecs.open(input_path, encoding=csv_encoding) as src, codecs.open(output_path, 'w', encoding=csv_encoding) as dst:
        rbconvert.rb_transform(src, dst)
    return output_path



rainbow_ut_prefix = 'ut_rbconvert_'

def run_conversion_test(query, input_table, testname, import_modules=None, join_csv_encoding=default_csv_encoding, delim='\t'):
    tmp_dir = tempfile.gettempdir()
    if not len(sys.path) or sys.path[0] != tmp_dir:
        sys.path.insert(0, tmp_dir)
    module_name = '{}{}_{}_{}'.format(rainbow_ut_prefix, time.time(), testname, random.randint(1, 100000000)).replace('.', '_')
    module_filename = '{}.py'.format(module_name)
    tmp_path = os.path.join(tmp_dir, module_filename)
    src = table_to_stream(input_table, delim)
    dst = io.StringIO()
    parse_to_py([query], tmp_path, delim, join_csv_encoding, import_modules)
    assert os.path.isfile(tmp_path) and os.access(tmp_path, os.R_OK)
    rbconvert = dynamic_import(module_name)
    rbconvert.rb_transform(src, dst)
    out_data = dst.getvalue()
    if len(out_data):
        out_lines = out_data[:-1].split('\n')
        out_table = [ln.split(delim) for ln in out_lines]
    else:
        out_table = []
    return out_table


def make_random_csv_entry(min_len, max_len, restricted_chars):
    strlen = random.randint(min_len, max_len)
    char_set = list(range(256))
    restricted_chars = [ord(c) for c in restricted_chars]
    char_set = [c for c in char_set if c not in restricted_chars]
    data = list()
    for i in xrange6(strlen):
        data.append(random.choice(char_set))
    pseudo_latin = bytes(bytearray(data)).decode('latin-1')
    return pseudo_latin


def generate_random_scenario(max_num_rows, max_num_cols, delims):
    num_rows = random.randint(1, max_num_rows)
    num_cols = random.randint(1, max_num_cols)
    delim = random.choice(delims)
    restricted_chars = ['\r', '\n'] + [delim]
    key_col = random.randint(0, num_cols - 1)
    good_keys = ['Hello', 'Avada Kedavra ', ' ??????', '128', '3q295 fa#(@*$*)', ' abcdefg ', 'NR', 'a1', 'a2']
    input_table = list()
    for r in xrange6(num_rows):
        input_table.append(list())
        for c in xrange6(num_cols):
            if c != key_col:
                input_table[-1].append(make_random_csv_entry(0, 20, restricted_chars))
            else:
                input_table[-1].append(random.choice(good_keys))

    output_table = list()
    target_key = random.choice(good_keys)
    if random.choice([True, False]):
        sql_op = '!='
        output_table = [row for row in input_table if row[key_col] != target_key]
    else:
        sql_op = '=='
        output_table = [row for row in input_table if row[key_col] == target_key]
    query = 'select * where a{} {} "{}"'.format(key_col + 1, sql_op, target_key)
    return (input_table, query, output_table, delim)


class TestEverything(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        tmp_dir = tempfile.gettempdir()
        old_unused = [f for f in os.listdir(tmp_dir) if f.startswith(rainbow_ut_prefix)]
        for name in old_unused:
            script_path = os.path.join(tmp_dir, name)
            os.remove(script_path)


    def compare_tables(self, canonic_table, test_table):
        self.assertEqual(len(canonic_table), len(test_table))
        for i in xrange6(len(canonic_table)):
            self.assertEqual(len(canonic_table[i]), len(test_table[i]))
            self.assertEqual(canonic_table[i], test_table[i])
        self.assertEqual(canonic_table, test_table)

    #TODO add degraded tests: empty table, one row table, empty result set etc

    def test_random_bin_tables(self):
        test_name = 'test_random_bin_tables'
        for subtest in xrange6(50):
            input_table, query, canonic_table, delim = generate_random_scenario(12, 12, ['\t', ',', ';'])
            test_table = run_conversion_test(query, input_table, test_name, delim=delim)
            self.compare_tables(canonic_table, test_table)


    def test_run1(self):
        test_name = 'test1'
        query = 'select NR, a1, len(a3) where int(a1) > 5'

        input_table = list()
        input_table.append(['5', 'haha', 'hoho'])
        input_table.append(['-20', 'haha', 'hioho'])
        input_table.append(['50', 'haha', 'dfdf'])
        input_table.append(['20', 'haha', ''])

        canonic_table = list()
        canonic_table.append(['3', '50', '4'])
        canonic_table.append(['4', '20', '0'])

        test_table = run_conversion_test(query, input_table, test_name)
        self.compare_tables(canonic_table, test_table)


    def test_run2(self):
        test_name = 'test2'
        query = '\tselect    distinct\ta2 where int(a1) > 10 #some#\t comments "with #text" "#"" '

        input_table = list()
        input_table.append(['5', 'haha', 'hoho'])
        input_table.append(['-20', 'haha', 'hioho'])
        input_table.append(['50', 'haha', 'dfdf'])
        input_table.append(['20', 'haha', ''])
        input_table.append(['8'])
        input_table.append(['3', '4', '1000', 'asdfasf', 'asdfsaf', 'asdfa'])
        input_table.append(['11', 'hoho', ''])
        input_table.append(['10', 'hihi', ''])
        input_table.append(['13', 'haha', ''])

        canonic_table = list()
        canonic_table.append(['haha'])
        canonic_table.append(['hoho'])

        test_table = run_conversion_test(query, input_table, test_name)
        self.compare_tables(canonic_table, test_table)

    def test_run3(self):
        test_name = 'test3'
        query = 'select \t  *  where flike(a2,\t"%a_a") order\tby int(a1)    desc   '
        input_table = list()
        input_table.append(['5', 'haha', 'hoho'])
        input_table.append(['-20', 'haha', 'hioho'])
        input_table.append(['50', 'haha', 'dfdf'])
        input_table.append(['20', 'haha', ''])
        input_table.append(['11', 'hoho', ''])
        input_table.append(['10', 'hihi', ''])
        input_table.append(['13', 'haha', ''])

        canonic_table = list()
        canonic_table.append(['50', 'haha', 'dfdf'])
        canonic_table.append(['20', 'haha', ''])
        canonic_table.append(['13', 'haha', ''])
        canonic_table.append(['5', 'haha', 'hoho'])
        canonic_table.append(['-20', 'haha', 'hioho'])


        test_table = run_conversion_test(query, input_table, test_name)
        self.compare_tables(canonic_table, test_table)

    def test_run4(self):
        test_name = 'test4'
        query = r'select int(math.sqrt(int(a1))), r"\'\"a   bc"'
        input_table = list()
        input_table.append(['0', 'haha', 'hoho'])
        input_table.append(['9'])
        input_table.append(['81', 'haha', 'dfdf'])
        input_table.append(['4', 'haha', 'dfdf', 'asdfa', '111'])

        canonic_table = list()
        canonic_table.append(['0', r"\'\"a   bc"])
        canonic_table.append(['3', r"\'\"a   bc"])
        canonic_table.append(['9', r"\'\"a   bc"])
        canonic_table.append(['2', r"\'\"a   bc"])

        test_table = run_conversion_test(query, input_table, test_name, ['math', 'os'])
        self.compare_tables(canonic_table, test_table)


    def test_run5(self):
        test_name = 'test5'
        query = 'select a2'
        input_table = list()
        input_table.append(['0', 'haha', 'hoho'])
        input_table.append(['9'])
        input_table.append(['81', 'haha', 'dfdf'])
        input_table.append(['4', 'haha', 'dfdf', 'asdfa', '111'])

        with self.assertRaises(Exception) as cm:
            run_conversion_test(query, input_table, test_name, ['math', 'os'])
        e = cm.exception
        self.assertTrue(str(e).find('No "a2" column at line: 2') != -1)


    def test_run6(self):
        test_name = 'test6'
        join_table_path = os.path.join(tempfile.gettempdir(), '{}_rhs_join_table.tsv'.format(test_name))

        join_table = list()
        join_table.append(['bicycle', 'legs'])
        join_table.append(['car', 'gas'])
        join_table.append(['plane', 'wings'])
        join_table.append(['boat', 'wind'])
        join_table.append(['rocket', 'some stuff'])

        table_to_file(join_table, join_table_path)

        query = r'select NR, * inner join {} on a2 == b1 where b2 != "haha" and int(a1) > -100 and len(b2) > 1 order by a2, int(a1)'.format(join_table_path)

        input_table = list()
        input_table.append(['5', 'car', 'lada'])
        input_table.append(['-20', 'car', 'Ferrari'])
        input_table.append(['50', 'plane', 'tu-134'])
        input_table.append(['20', 'boat', 'destroyer'])
        input_table.append(['10', 'boat', 'yacht'])
        input_table.append(['200', 'plane', 'boeing 737'])
        input_table.append(['80', 'train', 'Thomas'])

        canonic_table = list()
        canonic_table.append(['5', '10', 'boat', 'yacht', 'boat', 'wind'])
        canonic_table.append(['4', '20', 'boat', 'destroyer', 'boat', 'wind'])
        canonic_table.append(['2', '-20', 'car', 'Ferrari', 'car', 'gas'])
        canonic_table.append(['1', '5', 'car', 'lada', 'car', 'gas'])
        canonic_table.append(['3', '50', 'plane', 'tu-134', 'plane', 'wings'])
        canonic_table.append(['6', '200', 'plane', 'boeing 737', 'plane', 'wings'])

        test_table = run_conversion_test(query, input_table, test_name)
        self.compare_tables(canonic_table, test_table)


    def test_run7(self):
        test_name = 'test7'
        join_table_path = os.path.join(tempfile.gettempdir(), '{}_rhs_join_table.tsv'.format(test_name))

        join_table = list()
        join_table.append(['bicycle', 'legs'])
        join_table.append(['car', 'gas'])
        join_table.append(['plane', 'wings'])
        join_table.append(['rocket', 'some stuff'])

        table_to_file(join_table, join_table_path)

        query = r'select b1,b2,   a1 left join {} on a2 == b1 where b2 != "wings"'.format(join_table_path)

        input_table = list()
        input_table.append(['100', 'magic carpet', 'nimbus 3000'])
        input_table.append(['5', 'car', 'lada'])
        input_table.append(['-20', 'car', 'ferrari'])
        input_table.append(['50', 'plane', 'tu-134'])
        input_table.append(['20', 'boat', 'destroyer'])
        input_table.append(['10', 'boat', 'yacht'])
        input_table.append(['200', 'plane', 'boeing 737'])

        canonic_table = list()
        canonic_table.append(['None', 'None', '100'])
        canonic_table.append(['car', 'gas', '5'])
        canonic_table.append(['car', 'gas', '-20'])
        canonic_table.append(['None', 'None', '20'])
        canonic_table.append(['None', 'None', '10'])

        test_table = run_conversion_test(query, input_table, test_name)
        self.compare_tables(canonic_table, test_table)


    def test_run8(self):
        test_name = 'test8'
        join_table_path = os.path.join(tempfile.gettempdir(), '{}_rhs_join_table.tsv'.format(test_name))

        join_table = list()
        join_table.append(['bicycle', 'legs'])
        join_table.append(['car', 'gas'])
        join_table.append(['plane', 'wings'])
        join_table.append(['rocket', 'some stuff'])

        table_to_file(join_table, join_table_path)

        query = r'select b1,b2,   a1 strict left join {} on a2 == b1 where b2 != "wings"'.format(join_table_path)

        input_table = list()
        input_table.append(['5', 'car', 'lada'])
        input_table.append(['-20', 'car', 'ferrari'])
        input_table.append(['50', 'plane', 'tu-134'])
        input_table.append(['20', 'boat', 'destroyer'])
        input_table.append(['10', 'boat', 'yacht'])
        input_table.append(['200', 'plane', 'boeing 737'])
        input_table.append(['100', 'magic carpet', 'nimbus 3000'])

        with self.assertRaises(Exception) as cm:
            test_table = run_conversion_test(query, input_table, test_name)
        e = cm.exception
        self.assertTrue(str(e).find('all A table keys must be present in table B') != -1)


    def test_run9(self):
        test_name = 'test9'
        join_table_path = os.path.join(tempfile.gettempdir(), '{}_rhs_join_table.tsv'.format(test_name))

        join_table = list()
        join_table.append(['bicycle', 'legs'])
        join_table.append(['car', 'gas'])
        join_table.append(['plane', 'wings'])
        join_table.append(['plane', 'air'])
        join_table.append(['rocket', 'some stuff'])

        table_to_file(join_table, join_table_path)

        query = r'select b1,b2,a1 inner join {} on a2 == b1 where b1 != "car"'.format(join_table_path)

        input_table = list()
        input_table.append(['5', 'car', 'lada'])
        input_table.append(['-20', 'car', 'ferrari'])
        input_table.append(['50', 'plane', 'tu-134'])
        input_table.append(['200', 'plane', 'boeing 737'])

        with self.assertRaises(Exception) as cm:
            test_table = run_conversion_test(query, input_table, test_name)
        e = cm.exception
        self.assertTrue(str(e).find('Join column must be unique in right-hand-side "B" table') != -1)


    def test_run10(self):
        test_name = 'test10'
        query = 'select * where a3 =="hoho" or int(a1)==50 or a1 == "aaaa" or a2== "bbbbb" '

        input_table = list()
        input_table.append(['5', 'haha', 'hoho'])
        input_table.append(['-20', 'haha', 'hioho'])
        input_table.append(['50', 'haha', 'dfdf'])
        input_table.append(['20', 'haha', ''])

        canonic_table = list()
        canonic_table.append(['5', 'haha', 'hoho'])
        canonic_table.append(['50', 'haha', 'dfdf'])

        test_table = run_conversion_test(query, input_table, test_name)
        self.compare_tables(canonic_table, test_table)


    def test_run11(self):
        test_name = 'test11'
        query = 'select * where a2== "Наполеон" '

        input_table = list()
        input_table.append(['5', 'Петр Первый', 'hoho'])
        input_table.append(['-20', 'Екатерина Великая', 'hioho'])
        input_table.append(['50', 'Наполеон', 'dfdf'])
        input_table.append(['20', 'Наполеон', ''])

        canonic_table = list()
        canonic_table.append(['50', 'Наполеон', 'dfdf'])
        canonic_table.append(['20', 'Наполеон', ''])

        test_table = run_conversion_test(query, input_table, test_name, join_csv_encoding='utf-8')
        self.compare_tables(canonic_table, test_table)



def calc_file_md5(fname):
    import hashlib
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


class TestFiles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.old_dir = os.getcwd()
        script_dir = os.path.dirname(os.path.realpath(__file__))
        ut_dir = os.path.join(script_dir, 'unit_tests')
        os.chdir(ut_dir)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_dir)

    def test_all(self):
        import json
        ut_config_path = 'unit_tests.cfg'
        with codecs.open(ut_config_path, encoding='utf-8') as src:
            for test_no, line in enumerate(src, 1):
                config = json.loads(line)
                src_path = config['src_table']
                canonic_table = config['canonic_table']
                query = config['query']
                encoding = config.get('encoding', default_csv_encoding)
                delim = config.get('delim', 'TAB')
                if delim == 'TAB':
                    delim = '\t'
                result_table = run_file_query_test(query, src_path, str(test_no), csv_encoding=encoding, delim=delim)
                canonic_md5 = calc_file_md5(canonic_table)
                test_md5 = calc_file_md5(result_table)
                canonic_path = os.path.abspath(canonic_table)
                test_path = os.path.abspath(result_table) 
                self.assertEqual(test_md5, canonic_md5, msg='Tables missmatch. Canonic: {}; Actual: {}'.format(canonic_path, test_path))



class TestStringMethods(unittest.TestCase):

    def test_strip(self):
        a = 'v = "hello" #world  '
        a_strp = strip_comments(a)
        self.assertEqual(a_strp, 'v = "hello"')

    def test_strip2(self):
        a = r'''v = "hel\"lo" #w'or"ld  '''
        a_strp = strip_comments(a)
        self.assertEqual(a_strp, r'''v = "hel\"lo"''')

    def test_strip3(self):
        a = r'''v = "hello\\" #w'or"ld  '''
        a_strp = strip_comments(a)
        self.assertEqual(a_strp, r'''v = "hello\\"''')

    def test_strip4(self):
        a = ''' # a comment'''
        a_strp = strip_comments(a)
        self.assertEqual(a_strp, '')


if __name__ == '__main__':
    main()


