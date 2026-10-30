"""Streaming, non-executing reader for pinned MariaDB extended-INSERT dumps.

Strings remain bytes: category sortkeys are binary, not UTF-8 text. Unsupported
SQL is refused rather than interpreted or sent to an SQL engine.
"""
import gzip
import re
from vimfc.domain import Incompatible

TOKEN=re.compile(rb"'(?:\\.|''|[^'\\])*'|NULL|[-+]?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?|[(),;]")
ESCAPES={ord('0'):0,ord('n'):10,ord('r'):13,ord('t'):9,ord('b'):8,ord('Z'):26,
         ord('\\'):92,ord("'"):39,ord('"'):34}


def unquote(token):
    value=token[1:-1];result=bytearray();i=0
    while i<len(value):
        byte=value[i]
        if byte==92:
            i+=1
            if i==len(value) or value[i] not in ESCAPES:raise Incompatible('Unsupported MySQL string escape')
            result.append(ESCAPES[value[i]])
        elif byte==39 and i+1<len(value) and value[i+1]==39:result.append(byte);i+=1
        else:result.append(byte)
        i+=1
    return bytes(result)


def sql_rows(path,table):
    source=gzip.open(path,'rb');columns=[];creating=False
    try:
        while True:
            line=source.readline(8*1024*1024+1)
            if len(line)>8*1024*1024:raise Incompatible('SQL line exceeds streaming bound')
            if not line:raise Incompatible('Missing SQL CREATE/INSERT schema')
            if line.startswith(b'CREATE TABLE '):
                if line.strip()!=b'CREATE TABLE `'+table.encode()+b'` (':raise Incompatible('Unexpected SQL table')
                creating=True
            elif creating:
                m=re.match(rb'\s+`([^`]+)`\s',line)
                if m:columns.append(m[1].decode('ascii'))
                elif line.startswith(b') '):creating=False
            if line.startswith(b'INSERT INTO '):break
        if not columns or len(set(columns))!=len(columns):raise Incompatible('Invalid SQL columns')
    except BaseException:source.close();raise

    def iterator():
        nonlocal line
        values=[];inside=False;expect='row';insert=False;any_row=False
        try:
            while line:
                if len(line)>8*1024*1024:raise Incompatible('SQL line exceeds streaming bound')
                if line.startswith(b'INSERT INTO '):
                    if insert or inside:raise Incompatible('Unterminated SQL INSERT')
                    prefix=b'INSERT INTO `'+table.encode()+b'` VALUES'
                    if not line.startswith(prefix):raise Incompatible('Unexpected INSERT table/columns')
                    line=line[len(prefix):];insert=True;expect='row'
                elif not insert:
                    line=source.readline(8*1024*1024+1);continue
                pos=0
                for m in TOKEN.finditer(line):
                    if line[pos:m.start()].strip():raise Incompatible('Unsupported SQL token')
                    pos=m.end();token=m[0]
                    if token==b'(':
                        if expect!='row':raise Incompatible('Unexpected tuple')
                        values=[];inside=True;expect='value'
                    elif token==b')':
                        if not inside or expect!='separator' or len(values)!=len(columns):raise Incompatible('SQL tuple arity/syntax mismatch')
                        inside=False;expect='tuple_separator';any_row=True;yield tuple(values)
                    elif token==b',':
                        if expect=='separator':expect='value'
                        elif expect=='tuple_separator':expect='row'
                        else:raise Incompatible('Unexpected SQL comma')
                    elif token==b';':
                        if inside or expect!='tuple_separator':raise Incompatible('Incomplete SQL statement')
                        insert=False;expect='row'
                    else:
                        if not inside or expect!='value':raise Incompatible('Unexpected SQL value')
                        if token.startswith(b"'"):value=unquote(token)
                        elif token==b'NULL':value=None
                        elif re.fullmatch(rb'[-+]?[0-9]+',token):value=int(token)
                        else:value=token
                        values.append(value);expect='separator'
                if line[pos:].strip():raise Incompatible('Unparsed SQL bytes')
                line=source.readline(8*1024*1024+1)
            if insert or inside or not any_row:raise Incompatible('Truncated or empty SQL input')
        finally:source.close()
    return columns,iterator()
