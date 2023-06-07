# ffxiv-exdgetters.py
#
# Automagically labels most exd getter functions along with a hint indicating which sheet/sheet id its fetching from
#

import idaapi
import idc
import ida_bytes
import ida_nalt
import ida_struct
import ida_enum
import ida_kernwin
import ida_search
import ida_ida
import ida_typeinf

import os
from io import BufferedReader
import enum
import zlib
import json
import requests
import re


class SqPackCatergories(enum.IntEnum):
    COMMON = 0x0
    BGCOMMON = 0x1
    BG = 0x2
    CUT = 0x3
    CHARA = 0x4
    SHADER = 0x5
    UI = 0x6
    SOUND = 0x7
    VFX = 0x8
    UI_SCRIPT = 0x9
    EXD = 0xA
    GAME_SCRIPT = 0xB
    MUSIC = 0xC
    SQPACK_TEST = 0x12
    DEBUG = 0x13


class SqPackPlatformId(enum.IntEnum):
    Win32 = 0x0
    PS3 = 0x1
    PS4 = 0x2


class SqPackFileType(enum.IntEnum):
    Empty = 1,
    Standard = 2,
    Model = 3,
    Texture = 4,


class DatBlockType(enum.IntEnum):
    Compressed = 4713,
    Uncompressed = 32000,

class ExcelColumnDataType(enum.IntEnum):
    String = 0x0,
    Bool = 0x1,
    Int8 = 0x2,
    UInt8 = 0x3,
    Int16 = 0x4,
    UInt16 = 0x5,
    Int32 = 0x6,
    UInt32 = 0x7,
    # unused?
    Unk = 0x8,
    Float32 = 0x9,
    Int64 = 0xA,
    UInt64 = 0xB,
    # unused?
    Unk2 = 0xC,
    
    # 0 is read like data & 1, 1 is like data & 2, 2 = data & 4, etc...
    PackedBool0 = 0x19,
    PackedBool1 = 0x1A,
    PackedBool2 = 0x1B,
    PackedBool3 = 0x1C,
    PackedBool4 = 0x1D,
    PackedBool5 = 0x1E,
    PackedBool6 = 0x1F,
    PackedBool7 = 0x20


def column_data_type_to_ida_type(column_data_type: ExcelColumnDataType) -> str:
    if(column_data_type == ExcelColumnDataType.Bool):
        return 'bool'
    elif(column_data_type == ExcelColumnDataType.Int8):
        return '__int8'
    elif(column_data_type == ExcelColumnDataType.UInt8):
        return 'unsigned __int8'
    elif(column_data_type == ExcelColumnDataType.Int16):
        return '__int16'
    elif(column_data_type == ExcelColumnDataType.UInt16):
        return 'unsigned __int16'
    elif(column_data_type == ExcelColumnDataType.Int32):
        return 'int'
    elif(column_data_type == ExcelColumnDataType.UInt32):
        return 'unsigned int'
    elif(column_data_type == ExcelColumnDataType.Float32):
        return 'float'
    elif(column_data_type == ExcelColumnDataType.Int64):
        return '__int64'
    elif(column_data_type == ExcelColumnDataType.UInt64):
        return 'unsigned __int64'
    elif(column_data_type == ExcelColumnDataType.PackedBool0 or column_data_type == ExcelColumnDataType.PackedBool1 or column_data_type == ExcelColumnDataType.PackedBool2 or column_data_type == ExcelColumnDataType.PackedBool3 or column_data_type == ExcelColumnDataType.PackedBool4 or column_data_type == ExcelColumnDataType.PackedBool5 or column_data_type == ExcelColumnDataType.PackedBool6 or column_data_type == ExcelColumnDataType.PackedBool7):
        return 'unsigned __int8' # IDA doesn't support bitfields in decompilation, so we'll just use a byte. A different method would be to create an enum for each bitfield, but that's a lot of work that i cant be bothered doing.
    elif(column_data_type == ExcelColumnDataType.String):
        return '_DWORD' # strings are stored as a 4 byte offset to a string table, so we'll just use a 4 byte unknown type since another function handles reasign of strings.
    
def column_data_type_to_size(column_data_type: ExcelColumnDataType) -> int:
    if(column_data_type == ExcelColumnDataType.Bool or column_data_type == ExcelColumnDataType.Int8 or column_data_type == ExcelColumnDataType.UInt8 or column_data_type == ExcelColumnDataType.PackedBool0 or column_data_type == ExcelColumnDataType.PackedBool1 or column_data_type == ExcelColumnDataType.PackedBool2 or column_data_type == ExcelColumnDataType.PackedBool3 or column_data_type == ExcelColumnDataType.PackedBool4 or column_data_type == ExcelColumnDataType.PackedBool5 or column_data_type == ExcelColumnDataType.PackedBool6 or column_data_type == ExcelColumnDataType.PackedBool7):
        return 1
    elif(column_data_type == ExcelColumnDataType.Int16 or column_data_type == ExcelColumnDataType.UInt16):
        return 2
    elif(column_data_type == ExcelColumnDataType.Int32 or column_data_type == ExcelColumnDataType.UInt32 or column_data_type == ExcelColumnDataType.Float32 or column_data_type == ExcelColumnDataType.String):
        return 4
    elif(column_data_type == ExcelColumnDataType.Int64 or column_data_type == ExcelColumnDataType.UInt64):
        return 8

class SqPackFileInfo:
    def __init__(self, bytes: bytes, offset: int):
        self.header_size = int.from_bytes(bytes[0:4], byteorder='little')
        self.type = SqPackFileType(int.from_bytes(bytes[4:8], byteorder='little'))
        self.raw_file_size = int.from_bytes(bytes[8:12], byteorder='little')
        self.unknown = [int.from_bytes(bytes[12:16], byteorder='little'), int.from_bytes(bytes[16:20], byteorder='little')]
        self.number_of_blocks = int.from_bytes(bytes[20:24], byteorder='little')
        self.offset = offset


class DatStdFileBlockInfos:
    def __init__(self, bytes: bytes):
        self.offset = int.from_bytes(bytes[0:4], byteorder='little')
        self.compressed_size = int.from_bytes(bytes[4:6], byteorder='little')
        self.uncompressed_size = int.from_bytes(bytes[6:8], byteorder='little')


class DatBlockHeader:
    def __init__(self, bytes: bytes):
        self.size = int.from_bytes(bytes[0:4], byteorder='little')
        self.unknown1 = int.from_bytes(bytes[4:8], byteorder='little')
        self.block_data_size = int.from_bytes(bytes[8:12], byteorder='little')
        self.dat_block_type = int.from_bytes(bytes[12:16], byteorder='little')

    def __repr__(self):
        return f'''Size: {self.size} Unknown1: {self.unknown1} DatBlockType: {self.dat_block_type} BlockDataSize: {self.block_data_size}'''


class SqPackHeader:
    def __init__(self, file: BufferedReader):
        self.magic = file.read(8)
        self.platform_id = SqPackPlatformId(int.from_bytes(file.read(1), byteorder='little'))
        self.unknown = file.read(3)
        if (self.platform_id != SqPackPlatformId.PS3):
            self.size = int.from_bytes(file.read(4), byteorder='little')
            self.version = int.from_bytes(file.read(4), byteorder='little')
            self.type = int.from_bytes(file.read(4), byteorder='little')
        else:
            raise Exception('PS3 is not supported')

    def __repr__(self):
        return f'''Magic: {self.magic} Platform: {self.platform_id} Size: {self.size} Version: {self.version} Type: {self.type}'''


class SqPackIndexHeader:
    def __init__(self, bytes: bytes):
        self.size = int.from_bytes(bytes[0:4], byteorder='little')
        self.version = int.from_bytes(bytes[4:8], byteorder='little')
        self.index_data_offset = int.from_bytes(bytes[8:12], byteorder='little')
        self.index_data_size = int.from_bytes(bytes[12:16], byteorder='little')
        self.index_data_hash = bytes[16:80]
        self.number_of_data_file = int.from_bytes(bytes[80:84], byteorder='little')
        self.synonym_data_offset = int.from_bytes(bytes[84:88], byteorder='little')
        self.synonym_data_size = int.from_bytes(bytes[88:92], byteorder='little')
        self.synonym_data_hash = bytes[92:156]
        self.empty_block_data_offset = int.from_bytes(bytes[156:160], byteorder='little')
        self.empty_block_data_size = int.from_bytes(bytes[160:164], byteorder='little')
        self.empty_block_data_hash = bytes[164:228]
        self.dir_index_data_offset = int.from_bytes(bytes[228:232], byteorder='little')
        self.dir_index_data_size = int.from_bytes(bytes[232:236], byteorder='little')
        self.dir_index_data_hash = bytes[236:300]
        self.index_type = int.from_bytes(bytes[300:304], byteorder='little')
        self.reserved = bytes[304:960]
        self.hash = bytes[960:1024]

    def __repr__(self):
        return f'''Size: {self.size} Version: {self.version} Index Data Offset: {self.index_data_offset} Index Data Size: {self.index_data_size} Index Data Hash: {self.index_data_hash} Number Of Data File: {self.number_of_data_file} Synonym Data Offset: {self.synonym_data_offset} Synonym Data Size: {self.synonym_data_size} Synonym Data Hash: {self.synonym_data_hash} Empty Block Data Offset: {self.empty_block_data_offset} Empty Block Data Size: {self.empty_block_data_size} Empty Block Data Hash: {self.empty_block_data_hash} Dir Index Data Offset: {self.dir_index_data_offset} Dir Index Data Size: {self.dir_index_data_size} Dir Index Data Hash: {self.dir_index_data_hash} Index Type: {self.index_type} Reserved: {self.reserved} Hash: {self.hash}'''


class SqPackIndexHashTable:
    def __init__(self, bytes: bytes):
        self.hash = int.from_bytes(bytes[0:8], byteorder='little')
        self.data = int.from_bytes(bytes[8:12], byteorder='little')
        self.padding = int.from_bytes(bytes[12:16], byteorder='little')

    def is_synonym(self):
        return (self.data & 0b1) == 0b1

    def data_file_id(self):
        return (self.data & 0b1110) >> 1

    def data_file_offset(self):
        return (self.data & ~0xF) * 0x08

    def __repr__(self):
        return f'''Hash: {self.hash} Data: {self.data} Padding: {self.padding} Is Synonym: {self.is_synonym()} Data File ID: {self.data_file_id()} Data File Offset: {self.data_file_offset()}'''


class SqPack:
    def __init__(self, root: str, path: str):
        self.root = root
        self.path = path
        self.file = open(path, 'rb')
        self.header = SqPackHeader(self.file)

    def get_index_header(self):
        self.file.seek(self.header.size)
        return SqPackIndexHeader(self.file.read(1024))

    def get_index_hash_table(self, index_header: SqPackIndexHeader):
        self.file.seek(index_header.index_data_offset)
        entry_count = index_header.index_data_size // 16
        return [SqPackIndexHashTable(self.file.read(16)) for _ in range(entry_count)]

    def load_index_header(self):
        self.index_header = self.get_index_header()

    def load_hash_table(self):
        self.hash_table = self.get_index_hash_table(self.index_header)

    def discover_data_files(self):
        self.load_index_header()
        self.load_hash_table()
        self.data_files: list[str] = []
        for file in get_sqpack_files(self.root, self.path.rsplit('\\', 1)[0].split('\\')[-1]):
            for i in range(0, self.index_header.number_of_data_file):
                name = self.path.rsplit('.', 1)[0] + '.dat' + str(i)
                if file == name:
                    self.data_files.append(file)

    def read_file(self, offset: int):
        if self.path.rsplit('.', 1)[1][0:3] != 'dat':
            raise Exception('Not a data file')
        self.file.seek(offset)
        file_info_bytes = self.file.read(24)
        file_info = SqPackFileInfo(file_info_bytes, offset)
        data: list[bytes] = []
        if file_info.type == SqPackFileType.Empty:
            raise Exception(f'File located at 0x{hex(offset)} is empty.')
        elif file_info.type == SqPackFileType.Standard:
            data = self.read_standard_file(file_info)
        else:
            raise Exception('Type: ' + str(file_info.type) + ' not implemented.')
        return data

    def read_standard_file(self, file_info: SqPackFileInfo):
        block_bytes = self.file.read(file_info.number_of_blocks*8)
        data: list[bytes] = []
        for i in range(file_info.number_of_blocks):
            block = DatStdFileBlockInfos(block_bytes[i*8:i*8+8])
            self.file.seek(file_info.offset + file_info.header_size + block.offset)
            block_header = DatBlockHeader(self.file.read(16))
            if(block_header.dat_block_type == 32000):
                data.append(self.file.read(block_header.block_data_size))
            else:
                data.append(zlib.decompress(self.file.read(block_header.block_data_size), wbits=-15))

        return data
    
    def __repr__(self):
        return f'''Path: {os.path.join(self.root, 'sqpack', self.path)} Header: {self.header}'''


class Repository:
    def __init__(self, name: str, root: str):
        self.root = root
        self.name = name
        self.sqpacks: list[SqPack] = []
        self.index: dict[int, tuple[SqPackIndexHashTable, SqPack]] = {}
        self.expansion_id = 0
        self.get_expansion_id()

    def get_expansion_id(self):
        if (self.name.startswith('ex')):
            self.expansion_id = int(self.name.removeprefix('ex'))

    def parse_version(self):
        versionPath = ""
        if (self.name == 'ffxiv'):
            versionPath = os.path.join(self.root, 'ffxivgame.ver')
        else:
            versionPath = os.path.join(self.root, 'sqpack', self.name, self.name + '.ver')
        with open(versionPath, 'r') as f:
            self.version = f.read().strip()

    def setup_indexes(self):
        for file in get_sqpack_index(self.root, self.name):
            self.sqpacks.append(SqPack(self.root, file))

        for sqpack in self.sqpacks:
            sqpack.discover_data_files()
            for indexes in sqpack.hash_table:
                self.index[indexes.hash] = [indexes, sqpack]

    def get_index(self, hash: int):
        return self.index[hash]

    def get_file(self, hash: int):
        index, sqpack = self.get_index(hash)
        id = index.data_file_id()
        offset = index.data_file_offset()
        return SqPack(self.root, sqpack.data_files[id]).read_file(offset)
    
    def __repr__(self):
        return f'''Repository: {self.name} ({self.version}) - {self.expansion_id}'''


class GameData:
    def __init__(self, root: str):
        self.root = root
        self.repositories: dict[int, Repository] = {}
        self.setup()

    def get_repo_index(self, folder: str):
        if (folder == 'ffxiv'):
            return 0
        else:
            return int(folder.removeprefix('ex'))

    def setup(self):
        for folder in get_game_data_folders(self.root):
            self.repositories[self.get_repo_index(folder)] = Repository(folder, self.root)

        for folder in self.repositories:
            repo = self.repositories[folder]
            repo.parse_version()
            repo.setup_indexes()

    def get_file(self, file: 'ParsedFileName'):
        return self.repositories[self.get_repo_index(file.repo)].get_file(file.index)

    def __repr__(self):
        return f'''Repositories: {self.repositories}'''


class ExcelListFile:
    def __init__(self, data: list[bytes]):
        self.data = b''.join(data).split('\r\n'.encode('utf-8'))
        self.parse()

    def parse(self):
        self.header = self.data[0].decode('utf-8').split(',')
        self.version = int(self.header[1])
        self.data = self.data[1:]
        self.dict: dict[int, str] = {}
        for line in [x.decode('utf-8') for x in self.data]:
            if line == '':
                continue
            linearr = line.split(',')
            if linearr[1] == '-1':
                continue
            self.dict[int(linearr[1])] = linearr[0]

class ExcelHeader:
    def __init__(self, data: bytes):
        self.data = data
        self.parse()

    def parse(self):
        self.magic = self.data[0:4]
        self.version = int.from_bytes(self.data[4:6], 'big')
        self.data_offset = int.from_bytes(self.data[6:8], 'big')
        self.column_count = int.from_bytes(self.data[8:10], 'big')
        self.page_count = int.from_bytes(self.data[10:12], 'big')
        self.language_count = int.from_bytes(self.data[12:14], 'big')
        self.unknown1 = int.from_bytes(self.data[14:16], 'big')
        self.unknown2 = self.data[17]
        self.variant = self.data[18]
        self.unknown3 = int.from_bytes(self.data[19:20], 'big')
        self.row_count = int.from_bytes(self.data[20:24], 'big')
        self.unknown4 = [int.from_bytes(self.data[24:28], 'big'), int.from_bytes(self.data[28:32], 'big')]

    def __repr__(self):
        return f'''Header: {self.magic}, version: {self.version}, data_offset: {self.data_offset}, column_count: {self.column_count}, page_count: {self.page_count}, language_count: {self.language_count}, unknown1: {self.unknown1}, unknown2: {self.unknown2}, variant: {self.variant}, unknown3: {self.unknown3}, row_count: {self.row_count}, unknown4: {self.unknown4}'''

class ExcelColumnDefinition:
    def __init__(self, data: bytes):
        self.data = data
        self.parse()

    def parse(self):
        self.type = ExcelColumnDataType(int.from_bytes(self.data[0:2], 'big'))
        self.offset = int.from_bytes(self.data[2:4], 'big')

    def __repr__(self):
        return f'''[{self.type.name}, {self.offset:x}]'''
    
class ExcelDataPagination:
    def __init__(self, data: bytes):
        self.data = data
        self.parse()

    def parse(self):
        self.start_id = int.from_bytes(self.data[0:2], 'big')
        self.row_count = int.from_bytes(self.data[2:4], 'big')
    
    def __repr__(self):
        return f'''[{self.start_id:x}, {self.row_count}]'''

class ExcelHeaderFile:
    def __init__(self, data: list[bytes]):
        self.data = data[0]
        self.column_definitions: list[ExcelColumnDefinition] = []
        self.pagination: list[ExcelDataPagination] = []
        self.languages: list[int] = []
        self.header: ExcelHeader = None
        self.parse()
        
    def parse(self):
        self.header = ExcelHeader(self.data[0:32])
        if(self.header.magic != b'EXHF'):
            raise Exception('Invalid EXHF header')
        self.column_definitions: list[ExcelColumnDefinition] = []
        for i in range(self.header.column_count):
            self.column_definitions.append(ExcelColumnDefinition(self.data[32 + (i * 4):32 + ((i + 1) * 4)]))
        self.pagination: list[ExcelDataPagination] = []
        for i in range(self.header.page_count):
            self.pagination.append(ExcelDataPagination(self.data[32 + (self.header.column_count * 4) + (i * 4):32 + (self.header.column_count * 4) + ((i + 1) * 4)]))
        self.languages: list[int] = []
        for i in range(self.header.language_count):
            self.languages.append(self.data[32 + (self.header.column_count * 4) + (self.header.page_count * 4) + i])
    
    def map_names(self, names: dict[int, str]):
        mapped: dict[int, tuple[str, str]] = {}
        largest_offset_index: int = 0
        for i in range(self.header.column_count):
            if self.column_definitions[i].offset > self.column_definitions[largest_offset_index].offset:
                largest_offset_index = i

        size = self.column_definitions[largest_offset_index].offset + column_data_type_to_size(self.column_definitions[largest_offset_index].type)

        for i in range(self.header.column_count):
            if (self.column_definitions[i].offset in mapped and mapped[self.column_definitions[i].offset] is not None):
                [_, name] = mapped[self.column_definitions[i].offset]
                if name.split('_')[0] == 'Unknown':
                    continue
                if i not in names:
                    continue
                if column_data_type_to_ida_type(self.column_definitions[i].type) != 'unsigned __int8':
                    continue
                else:
                    mapped[self.column_definitions[i].offset] = (column_data_type_to_ida_type(self.column_definitions[i].type), f'{name}_{names[i]}')
            else:
                if i not in names:
                    mapped[self.column_definitions[i].offset] = (column_data_type_to_ida_type(self.column_definitions[i].type), f'Unknown_{self.column_definitions[i].offset:X}')
                else:
                    mapped[self.column_definitions[i].offset] = (column_data_type_to_ida_type(self.column_definitions[i].type), names[i])
        mapped = dict(sorted(mapped.items()))
        return [mapped, size]
    
    def __repr__(self):
        return f'''ExcelHeaderFile: {self.header} , {self.column_definitions} , {self.pagination} , {self.languages}'''


class ParsedFileName:
    def __init__(self, path: str):
        self.path = path.lower().strip()
        parts = self.path.split('/')
        self.category = parts[0]
        self.index = crc.calc_index(self.path)
        self.index2 = crc.calc_index2(self.path)
        self.repo = parts[1]
        if self.repo[0] != 'e' or self.repo[1] != 'x' or not self.repo[2].isdigit():
            self.repo = 'ffxiv'
    
    def __repr__(self):
        return f'''ParsedFileName: {self.path}, category: {self.category}, index: {self.index:X}, index2: {self.index2:X}, repo: {self.repo}'''


class Crc32:
    def __init__(self):
        self.poly = 0xEDB88320
        self.table = [0] * 256 * 16
        for i in range(256):
            res = i
            for j in range(16):
                for k in range(8):
                    if res & 1 == 1:
                        res = self.poly ^ (res >> 1)
                    else:
                        res = res >> 1
                self.table[i + j * 256] = res

    def calc(self, value: bytes):
        start = 0
        size = len(value)
        crc_local = 4294967295 ^ 0
        while size >= 16:
            a = self.table[(3*256) + value[start + 12]] ^ self.table[(2*256) + value[start + 13]] ^ self.table[(1*256) + value[start + 14]] ^ self.table[(0*256) + value[start + 15]]
            b = self.table[(7*256) + value[start + 8]] ^ self.table[(6*256) + value[start + 9]] ^ self.table[(5*256) + value[start + 10]] ^ self.table[(4*256) + value[start + 11]]
            c = self.table[(11*256) + value[start + 4]] ^ self.table[(10*256) + value[start + 5]] ^ self.table[(9*256) + value[start + 6]] ^ self.table[(8*256) + value[start + 7]]
            d = self.table[(15*256) + (self.byte(crc_local) ^ value[start])] ^ self.table[(14*256) + (self.byte(crc_local, 1) ^ value[start+1])] ^ self.table[(13*256) + (self.byte(crc_local, 2) ^ value[start+2])] ^ self.table[(12*256) + (self.byte(crc_local, 3) ^ value[start+3])]
            crc_local = d ^ c ^ b ^ a
            start += 16
            size -= 16

        while size > 0:
            crc_local = self.table[(crc_local ^ value[start]) & 0xFF] ^ (crc_local >> 8)
            start += 1
            size -= 1

        return ~(crc_local ^ 4294967295) % (1 << 32)
    
    def byte(self, number: int, i = 0):
        return (number & (0xff << (i * 8))) >> (i * 8)

    def calc_index(self, path: str):
        path_parts = path.split('/')
        filename = path_parts[-1]
        folder = path.rstrip(filename).rstrip('/')

        foldercrc = self.calc(folder.encode('utf-8'))
        filecrc = self.calc(filename.encode('utf-8'))

        return foldercrc << 32 | filecrc

    def calc_index2(self, path: str):
        return self.calc(path.encode('utf-8'))


crc = Crc32()


def get_game_data_folders(root: str):
    for folder in os.listdir(os.path.join(root, 'sqpack')):
        if (os.path.isdir(os.path.join(root, 'sqpack', folder))):
            yield folder


def get_files(path):
    files: list[bytes] = []
    for (dir_path, dir_names, file_names) in os.walk(path):
        files.extend(os.path.join(dir_path, file) for file in file_names)

    return files


def get_sqpack_files(root: str, path: str):
    for file in get_files(os.path.join(root, 'sqpack', path)):
        ext = file.split('.')[-1]
        if (ext.startswith('dat')):
            yield file


def get_sqpack_index(root: str, path: str):
    for file in get_files(os.path.join(root, 'sqpack', path)):
        if (file.endswith('.index')):
            yield file


def get_sqpack_index2(root: str, path: str):
    for file in get_files(os.path.join(root, 'sqpack', path)):
        if (file.endswith('.index2')):
            yield file


def get_definition_from_type(type: str, data: dict[str, str | int]):
    index = None
    if('index' in data):
        index = data['index']
    if(type == 'group'):
        return GroupDefinition(data)
    elif(type == 'repeat'):
        return RepeatDefinition(data['definition'], data['count'], index)
    else:
        raise Exception('Unknown type: ' + type)
    
def purge_name(name: str):
    return re.sub(r'[^a-zA-Z0-9_]', '', name)

class GroupDefinition:
    def __init__(self, data: dict[str, str | int]):
        self.data = data

        self.process()
    
    def process(self):
        self.members = []
        for member in self.data['members']:
            if('type' in member):
                self.members.append(get_definition_from_type(member['type'], member))
            else:
                self.members.append(member['name'])
    
    def flatten(self, count: int = None, index: int = None, append: bool = False):
        defs = []
        if(index == None):
            index = 0
        prepend = ''
        if(count != None):
            prepend = '_' + str(count)
        last_key = None
        for i in range(len(self.members)):
            if(isinstance(self.members[i], str)):
                defs.append([self.members[i] + prepend, index + i])
            else:
                if(last_key != None and last_key == self.members[i].data['name'] or len(self.members) == 1):
                    append = True
                else:
                    append = False
                last_key = self.members[i].data['name']
                defs.extend(self.members[i].flatten(count + i, index + len(defs), append))
        return defs
    
    def __repr__(self):
        return f'''{self.members}'''
        

class RepeatDefinition:
    def __init__(self, data: dict[str, str | int], count: int = 1, index: int = None):
        self.data = data
        self.count = count
        self.index = index
        self.process()
    
    def process(self):
        if('type' in self.data):
            self.type = self.data['type']
        else:
            self.name = self.data['name']
        if(hasattr(self, 'type')):
            self.sub_definition = get_definition_from_type(self.type, self.data)

    def flatten(self, count: int = None, index: int = None, append: bool = False):
        defs = []
        if(index == None):
            index = 0
        if(hasattr(self, 'index') and self.index != None):
            index = self.index
        prepend = ''
        if(count != None):
            prepend = '_' + str(count)
        if(hasattr(self, 'type')):
            for i in range(self.count):
                defs.extend(self.sub_definition.flatten(i, index + len(defs), True))
        else:
            for i in range(self.count):
                if append:
                    defs.append([self.name + '_' + str(i) + prepend, index + len(defs)])
                else:
                    defs.append([self.name + '_' + str(i), index + i])
        return defs

    def __repr__(self):
        if(hasattr(self, 'type')):
            return f'''{self.type}: {self.count}x {self.sub_definition}'''
        else:
            return f'''{self.name}: {self.count}x'''

class Definitions:
    def __init__(self, data: dict[str, str | int]):
        self.data = data
        self.process()
    
    def process(self):
        self.definitions = []
        for i in range(len(self.data)):
            if('type' in self.data[i]):
                self.definitions.append(get_definition_from_type(self.data[i]['type'], self.data[i]))
            else:
                index = 0
                if('index' in self.data[i]):
                    index = self.data[i]['index']
                self.definitions.append([self.data[i]['name'], index])

    def flatten(self) -> dict[int, str]:
        defs = []
        for definition in self.definitions:
            if(isinstance(definition, list)):
                defs.append(definition)
            else:
                defs.extend(definition.flatten())
        for i in range(len(defs)):
            [name, index] = defs[i]
            defs[i] = [purge_name(name), index]
        defsOut = {}
        for [name, index] in defs:
            defsOut[index] = name
        return defsOut
    
    def __repr__(self):
        return f'''{self.definitions}'''

class JsonExcelColumnDefinition:
    def __init__(self, name: str, mute: bool = False, supress: bool = False):
        self.name = name
        self.mute = mute
        self.supress = supress
        self.parse()
    
    def parse(self):
        if(self.mute == False):
            print("parsing " + self.name)
        self.req = requests.get(f'''https://raw.githubusercontent.com/xivapi/SaintCoinach/master/SaintCoinach/Definitions/{self.name}.json''')
        if(self.req.ok != True):
            if(self.supress == False):
                print("failed to get " + self.name)
            self.definitions = Definitions([])
            return
        self.json = json.loads(self.req.text)
        self.definitions = Definitions(self.json['definitions'])
        
    def __repr__(self):
        if(not hasattr(self, 'definitions')):
            return f'''{self.name}: None'''
        return f'''{self.name}: {self.definitions}'''

f = open(os.path.join(os.getenv('APPDATA'), 'XIVLauncher', 'launcherConfigV3.json'), 'r')

config = json.load(f)

f.close()

game_data = GameData(os.path.join(config['GamePath'], 'game'))

# nb: "pattern": "func suffix" OR None
exd_func_patterns = {
    "48 83 EC 28 48 8B 05 ? ? ? ? 44 8B C1 BA ? ? ? ? 48 8B 88 ? ? ? ? E8 ? ? ? ? 48 85 C0 75 05 48 83 C4 28 C3 48 8B 00 48 83 C4 28 C3": "Row",
    "48 83 EC 28 48 8B 05 ? ? ? ? BA ? ? ? ? 44 0F B6 C1 48 8B 88 ? ? ? ? E8 ? ? ? ? 48 85 C0 75 05 48 83 C4 28 C3 48 8B 00 48 83 C4 28 C3": "RowIndex",
    "48 83 EC 28 48 8B 05 ? ? ? ? 44 8D 81 ? ? ? ? BA ? ? ? ? 48 8B 88 ? ? ? ? E8 ? ? ? ? 48 85 C0 75 05 48 83 C4 28 C3 48 8B 00 48 83 C4 28 C3": "RowIndex",
    "48 83 EC 38 48 8B 05 ? ? ? ? 44 8B CA 44 8B C1 48 C7 44 24 ? ? ? ? ? BA ? ? ? ? 48 C7 44 24 ? ? ? ? ? 48 8B 88 ? ? ? ? E8 ? ? ? ? 48 85 C0 75 05 48 83 C4 38 C3 48 8B 00 48 83 C4 38 C3": "RowAndSubRowId",
    "48 83 EC 28 48 8B 05 ? ? ? ? BA ? ? ? ? 48 8B 88 ? ? ? ? E8 ? ? ? ? 48 85 C0 74 14 48 8B 10 48 8B C8 FF 52 08 84 C0 75 07 B0 01 48 83 C4 28 C3 32 C0 48 83 C4 28 C3": "SheetIndex",
    "48 83 EC 28 85 C9 74 20 48 8B 05 ? ? ? ? 44 8B C1 BA ? ? ? ? 48 8B 88 ? ? ? ? E8 ? ? ? ? 48 85 C0 75 07 33 C0 48 83 C4 28 C3 48 8B 00 48 83 C4 28 C3": "Row2",
    "48 83 EC 28 48 8B 05 ? ? ? ? 44 8B C1 BA ? ? ? ? 48 8B 88 ? ? ? ? E8 ? ? ? ? 48 85 C0 74 17 48 8B 08 48 85 C9 74 0F 8B 01 25 ? ? ? ? 48 03 C1 48 83 C4 28 C3 33 C0 48 83 C4 28 C3": None,

    # unsure if this is totally accurate but it looks to be the case
    "48 8B 05 ? ? ? ? BA ? ? ? ? 48 8B 88 ? ? ? ? E9 ? ? ? ?": "RowCount"
}

# todo: figure out how/where these exd getters are used
# .text:0000000140622200                         sub_140622200   proc near               ; CODE XREF: sub_14067D8E0+D3
# .text:0000000140622200 48 8B 05 F1 7F 46 01                    mov     rax, cs:qword_141A8A1F8
# .text:0000000140622207 BA 59 01 00 00                          mov     edx, 159h
# .text:000000014062220C 48 8B 88 E8 2B 00 00                    mov     rcx, [rax+2BE8h]
# .text:0000000140622213 E9 28 E2 E2 FF                          jmp     sub_140450440
# .text:0000000140622213                         sub_140622200   endp
exd_map = ExcelListFile(game_data.get_file(ParsedFileName('exd/root.exl'))).dict
exd_struct_map = {}

def get_tinfo_from_type(raw_type):
    """
    Retrieve a tinfo_t from a raw type string.
    """

    type_tinfo = idaapi.tinfo_t()
    ptr_tinfo = None

    ptr_count = raw_type.count("*")
    type = raw_type.rstrip("*")

    if not type_tinfo.get_named_type(idaapi.get_idati(), type):
        terminated = type + ";"
        if (idaapi.parse_decl(type_tinfo, idaapi.get_idati(), terminated, idaapi.PT_SIL) is None):
            print("! failed to parse type '{0}'".format(type))
            return None

    if ptr_count > 0:
        ptr_tinfo = idaapi.tinfo_t()
        for i in range(ptr_count):
            if not ptr_tinfo.create_ptr(type_tinfo):
                print("! failed to create pointer")
                return None
    else:
        ptr_tinfo = type_tinfo

    return ptr_tinfo

def get_idc_type_from_ida_type(type: str):
    if(type == 'unsigned __int8' or type == '__int8' or type == 'bool'):
        return ida_bytes.byte_flag()
    elif(type == 'unsigned __int16' or type == '__int16'):
        return ida_bytes.word_flag()
    elif(type == 'unsigned __int32' or type == '__int32' or type == 'int' or type == 'unsigned int' or type == '_DWORD'):
        return ida_bytes.dword_flag()
    elif(type == 'unsigned __int64' or type == '__int64'):
        return ida_bytes.qword_flag()
    elif(type == 'float'):
        return ida_bytes.float_flag()
    
def get_size_from_ida_type(type: str):
    if(type == 'unsigned __int8' or type == '__int8' or type == 'bool'):
        return 1
    elif(type == 'unsigned __int16' or type == '__int16'):
        return 2
    elif(type == 'unsigned __int32' or type == '__int32' or type == 'int' or type == 'unsigned int' or type == '_DWORD' or type == 'float'):
        return 4
    elif(type == 'unsigned __int64' or type == '__int64'):
        return 8

def do_structs():
    exd_headers: dict[int, dict[int, tuple[str, str]]] = {}

    exd_enum_struct = ida_enum.add_enum(idc.BADADDR, 'Component::Exd::SheetsEnum', 0)

    for key in exd_map:
        ida_enum.add_enum_member(exd_enum_struct, exd_map[key], key)
        exd_headers[key] = ExcelHeaderFile(game_data.get_file(ParsedFileName('exd/' + exd_map[key] + '.exh'))).map_names(JsonExcelColumnDefinition(exd_map[key], False, True).definitions.flatten())
    
    print('Making structs... please wait. This may take a while. Undo buffer will be cleared due to the large amount of changes.')

    for key in exd_headers:
        [exd_header, exd_size] = exd_headers[key]
        struct_name = f'Component::Exd::Sheets::{exd_map[key]}'
        struct_id = ida_struct.add_struc(-1, struct_name)
        struct_type = ida_struct.get_struc(struct_id)
        exd_struct_map[exd_map[key]] = struct_name
        for index in exd_header:
            [type, name] = exd_header[index]
            ida_struct.add_struc_member(struct_type, name, -1, get_idc_type_from_ida_type(type), None, get_size_from_ida_type(type))
            meminfo = ida_struct.get_member_by_name(struct_type, name)
            ida_struct.set_member_tinfo(struct_type, meminfo, 0, get_tinfo_from_type(type), 0)

def do_pattern(pattern, suffix, struct_parsed):
    ea = 0

    if suffix != None:
        print(f'Finding exd funcs of {suffix}... please wait.')

    while True:
        ea = ida_search.find_binary(ea + 1, ida_search.SEARCH_DOWN & 1 and ida_ida.cvar.inf.max_ea or ida_ida.cvar.inf.min_ea, pattern, 16, ida_search.SEARCH_DOWN)

        if ea == 0xFFFFFFFFFFFFFFFF:
            break

        # this is mega retarded but it works rofl
        ins = ida_search.find_binary(ea, ida_search.SEARCH_DOWN & 1 and ida_ida.cvar.inf.max_ea or ida_ida.cvar.inf.min_ea, "BA ? ? ? ?", 16, ida_search.SEARCH_DOWN)
        sheetIdx = idc.get_wide_dword(ins + 1)

        origName = idc.get_func_name(ea)

        # don't rename any funcs that are already named
        if origName[0:4] == "sub_":
            if exd_map.get(sheetIdx) == None:
                print(f"Func @ 0x{ea:X} references unknown sheet {sheetIdx}!")
                continue
        
            sheetName = exd_map[sheetIdx]

            if suffix == None:
                suffix = ""

            fnName = "Component::Exd::ExdModule_Get%s%s" % (exd_map[sheetIdx], suffix)

            uniquifier = 0
            while True:
                uniqueName = fnName + (f"_{uniquifier}" if uniquifier > 0 else "")
                            
                # check if this name is unique now
                if (idc.get_name_ea_simple(uniqueName) == idc.BADADDR and uniquifier > 0):
                    fnName = uniqueName
                    break
                                
                uniquifier += 1

            idc.set_name(ea, fnName)
            idc.set_cmt(ins, "Sheet: %s (%i)" % (sheetName, sheetIdx), 0)

        # TODO figure out why this doesn't work
        if struct_parsed:
            func_info = ida_typeinf.tinfo_t()
            funcdata = ida_typeinf.func_type_data_t()
            if not ida_nalt.get_tinfo(func_info, ea):
                print(func_info.is_funcptr() or func_info.is_func())
                print("Failed to get tinfo for %s @ %X" % (fnName, ea))
                continue

            if not func_info.get_func_details(funcdata):
                print("Failed to get func details for %s @ %X" % (fnName, ea))
                continue

            rettype = get_tinfo_from_type(f'{exd_struct_map[sheetIdx]} *')

            if rettype == None:
                print("Failed to get rettype for %s" % exd_struct_map[sheetIdx])
                continue

            funcdata.rettype = rettype

            if not func_info.create_func(funcdata):
                print("! failed to create function type for", fnName)
                return

            idaapi.apply_tinfo(ea, func_info, idaapi.TINFO_DEFINITE)


def run():
    sc_ver = requests.get('https://raw.githubusercontent.com/xivapi/SaintCoinach/master/SaintCoinach/Definitions/game.ver').text
    struct_parsed = True

    if sc_ver != game_data.repositories[0].version:
        cont = ida_kernwin.ask_yn(0, "SaintCoinach version mismatch! Expected %s, got %s. Use exd struct names anyway?" % (game_data.repositories[0].version, sc_ver))
        if cont != 1:
            struct_parsed = False

    if struct_parsed:
        do_structs()
    
    # todo: this doesnt find all getters, there's a few slightly different ones
    # along with others that call different virts in slightly different ways/different args
    for pattern, suffix in exd_func_patterns.items():
        if(suffix == None or suffix == 'RowCount' or suffix == 'SheetIndex'):
            do_pattern(pattern, suffix, False)
        else:
            do_pattern(pattern, suffix, struct_parsed)


class ffxiv_exdgetters_t(idaapi.plugin_t):
    flags = idaapi.PLUGIN_UNL

    wanted_name = "FFXIV - Annotate EXD Getters"
    wanted_hotkey = ""

    comment = 'Automagically names EXD getter funcs'
    help = 'no'
 
    def init(self):
        return idaapi.PLUGIN_OK
 
    def run(self, arg):
        print('run')
        run()
 
    def term(self):
        pass
 
def PLUGIN_ENTRY():
    return ffxiv_exdgetters_t()

run()