# SPDX-License-Identifier: GPL-2.0+
# Copyright (c) 2016 Google, Inc
#
# Base class for all entries
#

from collections import namedtuple
import importlib
import os
import pathlib
import sys
import time

from binman import bintool
from binman import elf
from dtoc import fdt_util
from patman import tools
from patman.tools import to_hex, to_hex_size
from patman import tout

modules = {}

# This is imported if needed
state = None

# An argument which can be passed to entries on the command line, in lieu of
# device-tree properties.
EntryArg = namedtuple('EntryArg', ['name', 'datatype'])

# Information about an entry for use when displaying summaries
EntryInfo = namedtuple('EntryInfo', ['indent', 'name', 'etype', 'size',
                                     'image_pos', 'uncomp_size', 'offset',
                                     'entry'])

class Entry(object):
    """An Entry in the section

    An entry corresponds to a single node in the device-tree description
    of the section. Each entry ends up being a part of the final section.
    Entries can be placed either right next to each other, or with padding
    between them. The type of the entry determines the data that is in it.

    This class is not used by itself. All entry objects are subclasses of
    Entry.

    Attributes:
        section: Section object containing this entry
        node: The node that created this entry
        offset: Offset of entry within the section, None if not known yet (in
            which case it will be calculated by Pack())
        size: Entry size in bytes, None if not known
        pre_reset_size: size as it was before ResetForPack(). This allows us to
            keep track of the size we started with and detect size changes
        uncomp_size: Size of uncompressed data in bytes, if the entry is
            compressed, else None
        contents_size: Size of contents in bytes, 0 by default
        align: Entry start offset alignment relative to the start of the
            containing section, or None
        align_size: Entry size alignment, or None
        align_end: Entry end offset alignment relative to the start of the
            containing section, or None
        pad_before: Number of pad bytes before the contents when it is placed
            in the containing section, 0 if none. The pad bytes become part of
            the entry.
        pad_after: Number of pad bytes after the contents when it is placed in
            the containing section, 0 if none. The pad bytes become part of
            the entry.
        data: Contents of entry (string of bytes). This does not include
            padding created by pad_before or pad_after. If the entry is
            compressed, this contains the compressed data.
        uncomp_data: Original uncompressed data, if this entry is compressed,
            else None
        compress: Compression algoithm used (e.g. 'lz4'), 'none' if none
        orig_offset: Original offset value read from node
        orig_size: Original size value read from node
        missing: True if this entry is missing its contents
        allow_missing: Allow children of this entry to be missing (used by
            subclasses such as Entry_section)
        allow_fake: Allow creating a dummy fake file if the blob file is not
            available. This is mainly used for testing.
        external: True if this entry contains an external binary blob
        bintools: Bintools used by this entry (only populated for Image)
        missing_bintools: List of missing bintools for this entry
        update_hash: True if this entry's "hash" subnode should be
            updated with a hash of the entry contents
        comp_bintool: Bintools used for compress and decompress data
        fake_fname: Fake filename, if one was created, else None
        required_props (dict of str): Properties which must be present. This can
            be added to by subclasses
        elf_fname (str): Filename of the ELF file, if this entry holds an ELF
            file, or is a binary file produced from an ELF file
        auto_write_symbols (bool): True to write ELF symbols into this entry's
            contents
    """
    fake_dir = None

    def __init__(self, section, etype, node, name_prefix='',
                 auto_write_symbols=False):
        # Put this here to allow entry-docs and help to work without libfdt
        global state
        from binman import state

        self.section = section
        self.etype = etype
        self._node = node
        self.name = node and (name_prefix + node.name) or 'none'
        self.offset = None
        self.size = None
        self.pre_reset_size = None
        self.uncomp_size = None
        self.data = None
        self.uncomp_data = None
        self.contents_size = 0
        self.align = None
        self.align_size = None
        self.align_end = None
        self.pad_before = 0
        self.pad_after = 0
        self.offset_unset = False
        self.image_pos = None
        self.extend_size = False
        self.compress = 'none'
        self.missing = False
        self.faked = False
        self.external = False
        self.allow_missing = False
        self.allow_fake = False
        self.bintools = {}
        self.missing_bintools = []
        self.update_hash = True
        self.fake_fname = None
        self.required_props = []
        self.comp_bintool = None
        self.elf_fname = None
        self.auto_write_symbols = auto_write_symbols

    @staticmethod
    def FindEntryClass(etype, expanded):
        """Look up the entry class for a node.

        Args:
            node_node: Path name of Node object containing information about
                       the entry to create (used for errors)
            etype:   Entry type to use
            expanded: Use the expanded version of etype

        Returns:
            The entry class object if found, else None if not found and expanded
                is True, else a tuple:
                    module name that could not be found
                    exception received
        """
        # Convert something like 'u-boot@0' to 'u_boot' since we are only
        # interested in the type.
        module_name = etype.replace('-', '_')

        if '@' in module_name:
            module_name = module_name.split('@')[0]
        if expanded:
            module_name += '_expanded'
        module = modules.get(module_name)

        # Also allow entry-type modules to be brought in from the etype directory.

        # Import the module if we have not already done so.
        if not module:
            try:
                module = importlib.import_module('binman.etype.' + module_name)
            except ImportError as e:
                if expanded:
                    return None
                return module_name, e
            modules[module_name] = module

        # Look up the expected class name
        return getattr(module, 'Entry_%s' % module_name)

    @staticmethod
    def Lookup(node_path, etype, expanded, missing_etype=False):
        """Look up the entry class for a node.

        Args:
            node_node (str): Path name of Node object containing information
                about the entry to create (used for errors)
            etype (str):   Entry type to use
            expanded (bool): Use the expanded version of etype
            missing_etype (bool): True to default to a blob etype if the
                requested etype is not found

        Returns:
            The entry class object if found, else None if not found and expanded
                is True

        Raise:
            ValueError if expanded is False and the class is not found
        """
        # Convert something like 'u-boot@0' to 'u_boot' since we are only
        # interested in the type.
        cls = Entry.FindEntryClass(etype, expanded)
        if cls is None:
            return None
        elif isinstance(cls, tuple):
            if missing_etype:
                cls = Entry.FindEntryClass('blob', False)
            if isinstance(cls, tuple): # This should not fail
                module_name, e = cls
                raise ValueError(
                    "Unknown entry type '%s' in node '%s' (expected etype/%s.py, error '%s'" %
                    (etype, node_path, module_name, e))
        return cls

    @staticmethod
    def Create(section, node, etype=None, expanded=False, missing_etype=False):
        """Create a new entry for a node.

        Args:
            section (entry_Section):  Section object containing this node
            node (Node): Node object containing information about the entry to
                create
            etype (str): Entry type to use, or None to work it out (used for
                tests)
            expanded (bool): Use the expanded version of etype
            missing_etype (bool): True to default to a blob etype if the
                requested etype is not found

        Returns:
            A new Entry object of the correct type (a subclass of Entry)
        """
        if not etype:
            etype = fdt_util.GetString(node, 'type', node.name)
        obj = Entry.Lookup(node.path, etype, expanded, missing_etype)
        if obj and expanded:
            # Check whether to use the expanded entry
            new_etype = etype + '-expanded'
            can_expand = not fdt_util.GetBool(node, 'no-expanded')
            if can_expand and obj.UseExpanded(node, etype, new_etype):
                etype = new_etype
            else:
                obj = None
        if not obj:
            obj = Entry.Lookup(node.path, etype, False, missing_etype)

        # Call its constructor to get the object we want.
        return obj(section, etype, node)

    def ReadNode(self):
        """Read entry information from the node

        This must be called as the first thing after the Entry is created.

        This reads all the fields we recognise from the node, ready for use.
        """
        self.ensure_props()
        if 'pos' in self._node.props:
            self.Raise("Please use 'offset' instead of 'pos'")
        if 'expand-size' in self._node.props:
            self.Raise("Please use 'extend-size' instead of 'expand-size'")
        self.offset = fdt_util.GetInt(self._node, 'offset')
        self.size = fdt_util.GetInt(self._node, 'size')
        self.orig_offset = fdt_util.GetInt(self._node, 'orig-offset')
        self.orig_size = fdt_util.GetInt(self._node, 'orig-size')
        if self.GetImage().copy_to_orig:
            self.orig_offset = self.offset
            self.orig_size = self.size

        # These should not be set in input files, but are set in an FDT map,
        # which is also read by this code.
        self.image_pos = fdt_util.GetInt(self._node, 'image-pos')
        self.uncomp_size = fdt_util.GetInt(self._node, 'uncomp-size')

        self.align = fdt_util.GetInt(self._node, 'align')
        if tools.not_power_of_two(self.align):
            raise ValueError("Node '%s': Alignment %s must be a power of two" %
                             (self._node.path, self.align))
        if self.section and self.align is None:
            self.align = self.section.align_default
        self.pad_before = fdt_util.GetInt(self._node, 'pad-before', 0)
        self.pad_after = fdt_util.GetInt(self._node, 'pad-after', 0)
        self.align_size = fdt_util.GetInt(self._node, 'align-size')
        if tools.not_power_of_two(self.align_size):
            self.Raise("Alignment size %s must be a power of two" %
                       self.align_size)
        self.align_end = fdt_util.GetInt(self._node, 'align-end')
        self.offset_unset = fdt_util.GetBool(self._node, 'offset-unset')
        self.extend_size = fdt_util.GetBool(self._node, 'extend-size')
        self.missing_msg = fdt_util.GetString(self._node, 'missing-msg')

        # This is only supported by blobs and sections at present
        self.compress = fdt_util.GetString(self._node, 'compress', 'none')

    def GetDefaultFilename(self):
        return None

    def GetFdts(self):
        """Get the device trees used by this entry

        Returns:
            Empty dict, if this entry is not a .dtb, otherwise:
            Dict:
                key: Filename from this entry (without the path)
                value: Tuple:
                    Entry object for this dtb
                    Filename of file containing this dtb
        """
        return {}

    def gen_entries(self):
        """Allow entries to generate other entries

        Some entries generate subnodes automatically, from which sub-entries
        are then created. This method allows those to be added to the binman
        definition for the current image. An entry which implements this method
        should call state.AddSubnode() to add a subnode and can add properties
        with state.AddString(), etc.

        An example is 'files', which produces a section containing a list of
        files.
        """
        pass

    def AddMissingProperties(self, have_image_pos):
        """Add new properties to the device tree as needed for this entry

        Args:
            have_image_pos: True if this entry has an image position. This can
                be False if its parent section is compressed, since compression
                groups all entries together into a compressed block of data,
                obscuring the start of each individual child entry
        """
        for prop in ['offset', 'size']:
            if not prop in self._node.props:
                state.AddZeroProp(self._node, prop)
        if have_image_pos and 'image-pos' not in self._node.props:
            state.AddZeroProp(self._node, 'image-pos')
        if self.GetImage().allow_repack:
            if self.orig_offset is not None:
                state.AddZeroProp(self._node, 'orig-offset', True)
            if self.orig_size is not None:
                state.AddZeroProp(self._node, 'orig-size', True)

        if self.compress != 'none':
            state.AddZeroProp(self._node, 'uncomp-size')

        if self.update_hash:
            err = state.CheckAddHashProp(self._node)
            if err:
                self.Raise(err)

    def SetCalculatedProperties(self):
        """Set the value of device-tree properties calculated by binman"""
        state.SetInt(self._node, 'offset', self.offset)
        state.SetInt(self._node, 'size', self.size)
        base = self.section.GetRootSkipAtStart() if self.section else 0
        if self.image_pos is not None:
            state.SetInt(self._node, 'image-pos', self.image_pos - base)
        if self.GetImage().allow_repack:
            if self.orig_offset is not None:
                state.SetInt(self._node, 'orig-offset', self.orig_offset, True)
            if self.orig_size is not None:
                state.SetInt(self._node, 'orig-size', self.orig_size, True)
        if self.uncomp_size is not None:
            state.SetInt(self._node, 'uncomp-size', self.uncomp_size)

        if self.update_hash:
            state.CheckSetHashValue(self._node, self.GetData)

    def ProcessFdt(self, fdt):
        """Allow entries to adjust the device tree

        Some entries need to adjust the device tree for their purposes. This
        may involve adding or deleting properties.

        Returns:
            True if processing is complete
            False if processing could not be completed due to a dependency.
                This will cause the entry to be retried after others have been
                called
        """
        return True

    def SetPrefix(self, prefix):
        """Set the name prefix for a node

        Args:
            prefix: Prefix to set, or '' to not use a prefix
        """
        if prefix:
            self.name = prefix + self.name

    def SetContents(self, data):
        """Set the contents of an entry

        This sets both the data and content_size properties

        Args:
            data: Data to set to the contents (bytes)
        """
        self.data = data
        self.contents_size = len(self.data)

    def ProcessContentsUpdate(self, data):
        """Update the contents of an entry, after the size is fixed

        This checks that the new data is the same size as the old. If the size
        has changed, this triggers a re-run of the packing algorithm.

        Args:
            data: Data to set to the contents (bytes)

        Raises:
            ValueError if the new data size is not the same as the old
        """
        size_ok = True
        new_size = len(data)
        if state.AllowEntryExpansion() and new_size > self.contents_size:
            # self.data will indicate the new size needed
            size_ok = False
        elif state.AllowEntryContraction() and new_size < self.contents_size:
            size_ok = False

        # If not allowed to change, try to deal with it or give up
        if size_ok:
            if new_size > self.contents_size:
                self.Raise('Cannot update entry size from %d to %d' %
                        (self.contents_size, new_size))

            # Don't let the data shrink. Pad it if necessary
            if size_ok and new_size < self.contents_size:
                data += tools.get_bytes(0, self.contents_size - new_size)

        if not size_ok:
            tout.debug("Entry '%s' size change from %s to %s" % (
                self._node.path, to_hex(self.contents_size),
                to_hex(new_size)))
        self.SetContents(data)
        return size_ok

    def ObtainContents(self, skip_entry=None, fake_size=0):
        """Figure out the contents of an entry.

        Args:
            skip_entry (Entry): Entry to skip when obtaining section contents
            fake_size (int): Size of fake file to create if needed

        Returns:
            True if the contents were found, False if another call is needed
            after the other entries are processed.
        """
        # No contents by default: subclasses can implement this
        return True

    def ResetForPack(self):
        """Reset offset/size fields so that packing can be done again"""
        self.Detail('ResetForPack: offset %s->%s, size %s->%s' %
                    (to_hex(self.offset), to_hex(self.orig_offset),
                     to_hex(self.size), to_hex(self.orig_size)))
        self.pre_reset_size = self.size
        self.offset = self.orig_offset
        self.size = self.orig_size

    def Pack(self, offset):
        """Figure out how to pack the entry into the section

        Most of the time the entries are not fully specified. There may be
        an alignment but no size. In that case we take the size from the
        contents of the entry.

        If an entry has no hard-coded offset, it will be placed at @offset.

        Once this function is complete, both the offset and size of the
        entry will be know.

        Args:
            Current section offset pointer

        Returns:
            New section offset pointer (after this entry)
        """
        self.Detail('Packing: offset=%s, size=%s, content_size=%x' %
                    (to_hex(self.offset), to_hex(self.size),
                     self.contents_size))
        if self.offset is None:
            if self.offset_unset:
                self.Raise('No offset set with offset-unset: should another '
                           'entry provide this correct offset?')
        elif self.offset > offset:
            offset = self.offset
        self.offset = tools.align(offset, self.align)
        needed = self.pad_before + self.contents_size + self.pad_after
        needed = tools.align(needed, self.align_size)
        size = self.size
        if not size:
            size = needed
        new_offset = self.offset + size
        aligned_offset = tools.align(new_offset, self.align_end)
        if aligned_offset != new_offset:
            size = aligned_offset - self.offset
            new_offset = aligned_offset

        if not self.size:
            self.size = size

        if self.size < needed:
            self.Raise("Entry contents size is %#x (%d) but entry size is "
                       "%#x (%d)" % (needed, needed, self.size, self.size))
        # Check that the alignment is correct. It could be wrong if the
        # and offset or size values were provided (i.e. not calculated), but
        # conflict with the provided alignment values
        if self.size != tools.align(self.size, self.align_size):
            self.Raise("Size %#x (%d) does not match align-size %#x (%d)" %
                  (self.size, self.size, self.align_size, self.align_size))
        if self.offset != tools.align(self.offset, self.align):
            self.Raise("Offset %#x (%d) does not match align %#x (%d)" %
                  (self.offset, self.offset, self.align, self.align))
        self.Detail('   - packed: offset=%#x, size=%#x, content_size=%#x, next_offset=%x' %
                    (self.offset, self.size, self.contents_size, new_offset))

        return new_offset

    def Raise(self, msg):
        """Convenience function to raise an error referencing a node"""
        raise ValueError("Node '%s': %s" % (self._node.path, msg))

    def Info(self, msg):
        """Convenience function to log info referencing a node"""
        tag = "Info '%s'" % self._node.path
        tout.detail('%30s: %s' % (tag, msg))

    def Detail(self, msg):
        """Convenience function to log detail referencing a node"""
        tag = "Node '%s'" % self._node.path
        tout.detail('%30s: %s' % (tag, msg))

    def GetEntryArgsOrProps(self, props, required=False):
        """Return the values of a set of properties

        Args:
            props: List of EntryArg objects

        Raises:
            ValueError if a property is not found
        """
        values = []
        missing = []
        for prop in props:
            python_prop = prop.name.replace('-', '_')
            if hasattr(self, python_prop):
                value = getattr(self, python_prop)
            else:
                value = None
            if value is None:
                value = self.GetArg(prop.name, prop.datatype)
            if value is None and required:
                missing.append(prop.name)
            values.append(value)
        if missing:
            self.GetImage().MissingArgs(self, missing)
        return values

    def GetPath(self):
        """Get the path of a node

        Returns:
            Full path of the node for this entry
        """
        return self._node.path

    def GetData(self, required=True):
        """Get the contents of an entry

        Args:
            required: True if the data must be present, False if it is OK to
                return None

        Returns:
            bytes content of the entry, excluding any padding. If the entry is
                compressed, the compressed data is returned
        """
        self.Detail('GetData: size %s' % to_hex_size(self.data))
        return self.data

    def GetPaddedData(self, data=None):
        """Get the data for an entry including any padding

        Gets the entry data and uses its section's pad-byte value to add padding
        before and after as defined by the pad-before and pad-after properties.

        This does not consider alignment.

        Returns:
            Contents of the entry along with any pad bytes before and
            after it (bytes)
        """
        if data is None:
            data = self.GetData()
        return self.section.GetPaddedDataForEntry(self, data)

    def GetOffsets(self):
        """Get the offsets for siblings

        Some entry types can contain information about the position or size of
        other entries. An example of this is the Intel Flash Descriptor, which
        knows where the Intel Management Engine section should go.

        If this entry knows about the position of other entries, it can specify
        this by returning values here

        Returns:
            Dict:
                key: Entry type
                value: List containing position and size of the given entry
                    type. Either can be None if not known
        """
        return {}

    def SetOffsetSize(self, offset, size):
        """Set the offset and/or size of an entry

        Args:
            offset: New offset, or None to leave alone
            size: New size, or None to leave alone
        """
        if offset is not None:
            self.offset = offset
        if size is not None:
            self.size = size

    def SetImagePos(self, image_pos):
        """Set the position in the image

        Args:
            image_pos: Position of this entry in the image
        """
        self.image_pos = image_pos + self.offset

    def ProcessContents(self):
        """Do any post-packing updates of entry contents

        This function should call ProcessContentsUpdate() to update the entry
        contents, if necessary, returning its return value here.

        Args:
            data: Data to set to the contents (bytes)

        Returns:
            True if the new data size is OK, False if expansion is needed

        Raises:
            ValueError if the new data size is not the same as the old and
                state.AllowEntryExpansion() is False
        """
        return True

    def WriteSymbols(self, section):
        """Write symbol values into binary files for access at run time

        Args:
          section: Section containing the entry
        """
        if self.auto_write_symbols:
            # Check if we are writing symbols into an ELF file
            is_elf = self.GetDefaultFilename() == self.elf_fname
            elf.LookupAndWriteSymbols(self.elf_fname, self, section.GetImage(),
                                      is_elf)

    def CheckEntries(self):
        """Check that the entry offsets are correct

        This is used for entries which have extra offset requirements (other
        than having to be fully inside their section). Sub-classes can implement
        this function and raise if there is a problem.
        """
        pass

    @staticmethod
    def GetStr(value):
        if value is None:
            return '<none>  '
        return '%08x' % value

    @staticmethod
    def WriteMapLine(fd, indent, name, offset, size, image_pos):
        print('%s  %s%s  %s  %s' % (Entry.GetStr(image_pos), ' ' * indent,
                                    Entry.GetStr(offset), Entry.GetStr(size),
                                    name), file=fd)

    def WriteMap(self, fd, indent):
        """Write a map of the entry to a .map file

        Args:
            fd: File to write the map to
            indent: Curent indent level of map (0=none, 1=one level, etc.)
        """
        self.WriteMapLine(fd, indent, self.name, self.offset, self.size,
                          self.image_pos)

    # pylint: disable=assignment-from-none
    def GetEntries(self):
        """Return a list of entries contained by this entry

        Returns:
            List of entries, or None if none. A normal entry has no entries
                within it so will return None
        """
        return None

    def FindEntryByNode(self, find_node):
        """Find a node in an entry, searching all subentries

        This does a recursive search.

        Args:
            find_node (fdt.Node): Node to find

        Returns:
            Entry: entry, if found, else None
        """
        entries = self.GetEntries()
        if entries:
            for entry in entries.values():
                if entry._node == find_node:
                    return entry
                found = entry.FindEntryByNode(find_node)
                if found:
                    return found

        return None

    def GetArg(self, name, datatype=str):
        """Get the value of an entry argument or device-tree-node property

        Some node properties can be provided as arguments to binman. First check
        the entry arguments, and fall back to the device tree if not found

        Args:
            name: Argument name
            datatype: Data type (str or int)

        Returns:
            Value of argument as a string or int, or None if no value

        Raises:
            ValueError if the argument cannot be converted to in
        """
        value = state.GetEntryArg(name)
        if value is not None:
            if datatype == int:
                try:
                    value = int(value)
                except ValueError:
                    self.Raise("Cannot convert entry arg '%s' (value '%s') to integer" %
                               (name, value))
            elif datatype == str:
                pass
            else:
                raise ValueError("GetArg() internal error: Unknown data type '%s'" %
                                 datatype)
        else:
            value = fdt_util.GetDatatype(self._node, name, datatype)
        return value

    @staticmethod
    def WriteDocs(modules, test_missing=None):
        """Write out documentation about the various entry types to stdout

        Args:
            modules: List of modules to include
            test_missing: Used for testing. This is a module to report
                as missing
        """
        print('''Binman Entry Documentation
===========================

This file describes the entry types supported by binman. These entry types can
be placed in an image one by one to build up a final firmware image. It is
fairly easy to create new entry types. Just add a new file to the 'etype'
directory. You can use the existing entries as examples.

Note that some entries are subclasses of others, using and extending their
features to produce new behaviours.


''')
        modules = sorted(modules)

        # Don't show the test entry
        if '_testing' in modules:
            modules.remove('_testing')
        missing = []
        for name in modules:
            module = Entry.Lookup('WriteDocs', name, False)
            docs = getattr(module, '__doc__')
            if test_missing == name:
                docs = None
            if docs:
                lines = docs.splitlines()
                first_line = lines[0]
                rest = [line[4:] for line in lines[1:]]
                hdr = 'Entry: %s: %s' % (name.replace('_', '-'), first_line)

                # Create a reference for use by rST docs
                ref_name = f'etype_{module.__name__[6:]}'.lower()
                print('.. _%s:' % ref_name)
                print()
                print(hdr)
                print('-' * len(hdr))
                print('\n'.join(rest))
                print()
                print()
            else:
                missing.append(name)

        if missing:
            raise ValueError('Documentation is missing for modules: %s' %
                             ', '.join(missing))

    def GetUniqueName(self):
        """Get a unique name for a node

        Returns:
            String containing a unique name for a node, consisting of the name
            of all ancestors (starting from within the 'binman' node) separated
            by a dot ('.'). This can be useful for generating unique filesnames
            in the output directory.
        """
        name = self.name
        node = self._node
        while node.parent:
            node = node.parent
            if node.name in ('binman', '/'):
                break
            name = '%s.%s' % (node.name, name)
        return name

    def extend_to_limit(self, limit):
        """Extend an entry so that it ends at the given offset limit"""
        if self.offset + self.size < limit:
            self.size = limit - self.offset
            # Request the contents again, since changing the size requires that
            # the data grows. This should not fail, but check it to be sure.
            if not self.ObtainContents():
                self.Raise('Cannot obtain contents when expanding entry')

    def HasSibling(self, name):
        """Check if there is a sibling of a given name

        Returns:
            True if there is an entry with this name in the the same section,
                else False
        """
        return name in self.section.GetEntries()

    def GetSiblingImagePos(self, name):
        """Return the image position of the given sibling

        Returns:
            Image position of sibling, or None if the sibling has no position,
                or False if there is no such sibling
        """
        if not self.HasSibling(name):
            return False
        return self.section.GetEntries()[name].image_pos

    @staticmethod
    def AddEntryInfo(entries, indent, name, etype, size, image_pos,
                     uncomp_size, offset, entry):
        """Add a new entry to the entries list

        Args:
            entries: List (of EntryInfo objects) to add to
            indent: Current indent level to add to list
            name: Entry name (string)
            etype: Entry type (string)
            size: Entry size in bytes (int)
            image_pos: Position within image in bytes (int)
            uncomp_size: Uncompressed size if the entry uses compression, else
                None
            offset: Entry offset within parent in bytes (int)
            entry: Entry object
        """
        entries.append(EntryInfo(indent, name, etype, size, image_pos,
                                 uncomp_size, offset, entry))

    def ListEntries(self, entries, indent):
        """Add files in this entry to the list of entries

        This can be overridden by subclasses which need different behaviour.

        Args:
            entries: List (of EntryInfo objects) to add to
            indent: Current indent level to add to list
        """
        self.AddEntryInfo(entries, indent, self.name, self.etype, self.size,
                          self.image_pos, self.uncomp_size, self.offset, self)

    def ReadData(self, decomp=True, alt_format=None):
        """Read the data for an entry from the image

        This is used when the image has been read in and we want to extract the
        data for a particular entry from that image.

        Args:
            decomp: True to decompress any compressed data before returning it;
                False to return the raw, uncompressed data

        Returns:
            Entry data (bytes)
        """
        # Use True here so that we get an uncompressed section to work from,
        # although compressed sections are currently not supported
        tout.debug("ReadChildData section '%s', entry '%s'" %
                   (self.section.GetPath(), self.GetPath()))
        data = self.section.ReadChildData(self, decomp, alt_format)
        return data

    def ReadChildData(self, child, decomp=True, alt_format=None):
        """Read the data for a particular child entry

        This reads data from the parent and extracts the piece that relates to
        the given child.

        Args:
            child (Entry): Child entry to read data for (must be valid)
            decomp (bool): True to decompress any compressed data before
                returning it; False to return the raw, uncompressed data
            alt_format (str): Alternative format to read in, or None

        Returns:
            Data for the child (bytes)
        """
        pass

    def LoadData(self, decomp=True):
        data = self.ReadData(decomp)
        self.contents_size = len(data)
        self.ProcessContentsUpdate(data)
        self.Detail('Loaded data size %x' % len(data))

    def GetAltFormat(self, data, alt_format):
        """Read the data for an extry in an alternative format

        Supported formats are list in the documentation for each entry. An
        example is fdtmap which provides .

        Args:
            data (bytes): Data to convert (this should have been produced by the
                entry)
            alt_format (str): Format to use

        """
        pass

    def GetImage(self):
        """Get the image containing this entry

        Returns:
            Image object containing this entry
        """
        return self.section.GetImage()

    def WriteData(self, data, decomp=True):
        """Write the data to an entry in the image

        This is used when the image has been read in and we want to replace the
        data for a particular entry in that image.

        The image must be re-packed and written out afterwards.

        Args:
            data: Data to replace it with
            decomp: True to compress the data if needed, False if data is
                already compressed so should be used as is

        Returns:
            True if the data did not result in a resize of this entry, False if
                 the entry must be resized
        """
        if self.size is not None:
            self.contents_size = self.size
        else:
            self.contents_size = self.pre_reset_size
        ok = self.ProcessContentsUpdate(data)
        self.Detail('WriteData: size=%x, ok=%s' % (len(data), ok))
        section_ok = self.section.WriteChildData(self)
        return ok and section_ok

    def WriteChildData(self, child):
        """Handle writing the data in a child entry

        This should be called on the child's parent section after the child's
        data has been updated. It should update any data structures needed to
        validate that the update is successful.

        This base-class implementation does nothing, since the base Entry object
        does not have any children.

        Args:
            child: Child Entry that was written

        Returns:
            True if the section could be updated successfully, False if the
                data is such that the section could not update
        """
        return True

    def GetSiblingOrder(self):
        """Get the relative order of an entry amoung its siblings

        Returns:
            'start' if this entry is first among siblings, 'end' if last,
                otherwise None
        """
        entries = list(self.section.GetEntries().values())
        if entries:
            if self == entries[0]:
                return 'start'
            elif self == entries[-1]:
                return 'end'
        return 'middle'

    def SetAllowMissing(self, allow_missing):
        """Set whether a section allows missing external blobs

        Args:
            allow_missing: True if allowed, False if not allowed
        """
        # This is meaningless for anything other than sections
        pass

    def SetAllowFakeBlob(self, allow_fake):
        """Set whether a section allows to create a fake blob

        Args:
            allow_fake: True if allowed, False if not allowed
        """
        self.allow_fake = allow_fake

    def CheckMissing(self, missing_list):
        """Check if any entries in this section have missing external blobs

        If there are missing blobs, the entries are added to the list

        Args:
            missing_list: List of Entry objects to be added to
        """
        if self.missing:
            missing_list.append(self)

    def check_fake_fname(self, fname, size=0):
        """If the file is missing and the entry allows fake blobs, fake it

        Sets self.faked to True if faked

        Args:
            fname (str): Filename to check
            size (int): Size of fake file to create

        Returns:
            tuple:
                fname (str): Filename of faked file
                bool: True if the blob was faked, False if not
        """
        if self.allow_fake and not pathlib.Path(fname).is_file():
            if not self.fake_fname:
                outfname = os.path.join(self.fake_dir, os.path.basename(fname))
                with open(outfname, "wb") as out:
                    out.truncate(size)
                tout.info(f"Entry '{self._node.path}': Faked blob '{outfname}'")
                self.fake_fname = outfname
            self.faked = True
            return self.fake_fname, True
        return fname, False

    def CheckFakedBlobs(self, faked_blobs_list):
        """Check if any entries in this section have faked external blobs

        If there are faked blobs, the entries are added to the list

        Args:
            fake_blobs_list: List of Entry objects to be added to
        """
        # This is meaningless for anything other than blobs
        pass

    def GetAllowMissing(self):
        """Get whether a section allows missing external blobs

        Returns:
            True if allowed, False if not allowed
        """
        return self.allow_missing

    def record_missing_bintool(self, bintool):
        """Record a missing bintool that was needed to produce this entry

        Args:
            bintool (Bintool): Bintool that was missing
        """
        if bintool not in self.missing_bintools:
            self.missing_bintools.append(bintool)

    def check_missing_bintools(self, missing_list):
        """Check if any entries in this section have missing bintools

        If there are missing bintools, these are added to the list

        Args:
            missing_list: List of Bintool objects to be added to
        """
        for bintool in self.missing_bintools:
            if bintool not in missing_list:
                missing_list.append(bintool)


    def GetHelpTags(self):
        """Get the tags use for missing-blob help

        Returns:
            list of possible tags, most desirable first
        """
        return list(filter(None, [self.missing_msg, self.name, self.etype]))

    def CompressData(self, indata):
        """Compress data according to the entry's compression method

        Args:
            indata: Data to compress

        Returns:
            Compressed data
        """
        self.uncomp_data = indata
        if self.compress != 'none':
            self.uncomp_size = len(indata)
            if self.comp_bintool.is_present():
                data = self.comp_bintool.compress(indata)
            else:
                self.record_missing_bintool(self.comp_bintool)
                data = tools.get_bytes(0, 1024)
        else:
            data = indata
        return data

    def DecompressData(self, indata):
        """Decompress data according to the entry's compression method

        Args:
            indata: Data to decompress

        Returns:
            Decompressed data
        """
        if self.compress != 'none':
            if self.comp_bintool.is_present():
                data = self.comp_bintool.decompress(indata)
                self.uncomp_size = len(data)
            else:
                self.record_missing_bintool(self.comp_bintool)
                data = tools.get_bytes(0, 1024)
        else:
            data = indata
        self.uncomp_data = data
        return data

    @classmethod
    def UseExpanded(cls, node, etype, new_etype):
        """Check whether to use an expanded entry type

        This is called by Entry.Create() when it finds an expanded version of
        an entry type (e.g. 'u-boot-expanded'). If this method returns True then
        it will be used (e.g. in place of 'u-boot'). If it returns False, it is
        ignored.

        Args:
            node:     Node object containing information about the entry to
                      create
            etype:    Original entry type being used
            new_etype: New entry type proposed

        Returns:
            True to use this entry type, False to use the original one
        """
        tout.info("Node '%s': etype '%s': %s selected" %
                  (node.path, etype, new_etype))
        return True

    def CheckAltFormats(self, alt_formats):
        """Add any alternative formats supported by this entry type

        Args:
            alt_formats (dict): Dict to add alt_formats to:
                key: Name of alt format
                value: Help text
        """
        pass

    def AddBintools(self, btools):
        """Add the bintools used by this entry type

        Args:
            btools (dict of Bintool):

        Raise:
            ValueError if compression algorithm is not supported
        """
        algo = self.compress
        if algo != 'none':
            algos = ['bzip2', 'gzip', 'lz4', 'lzma', 'lzo', 'xz', 'zstd']
            if algo not in algos:
                raise ValueError("Unknown algorithm '%s'" % algo)
            names = {'lzma': 'lzma_alone', 'lzo': 'lzop'}
            name = names.get(self.compress, self.compress)
            self.comp_bintool = self.AddBintool(btools, name)

    @classmethod
    def AddBintool(self, tools, name):
        """Add a new bintool to the tools used by this etype

        Args:
            name: Name of the tool
        """
        btool = bintool.Bintool.create(name)
        tools[name] = btool
        return btool

    def SetUpdateHash(self, update_hash):
        """Set whether this entry's "hash" subnode should be updated

        Args:
            update_hash: True if hash should be updated, False if not
        """
        self.update_hash = update_hash

    def collect_contents_to_file(self, entries, prefix, fake_size=0):
        """Put the contents of a list of entries into a file

        Args:
            entries (list of Entry): Entries to collect
            prefix (str): Filename prefix of file to write to
            fake_size (int): Size of fake file to create if needed

        If any entry does not have contents yet, this function returns False
        for the data.

        Returns:
            Tuple:
                bytes: Concatenated data from all the entries (or None)
                str: Filename of file written (or None if no data)
                str: Unique portion of filename (or None if no data)
        """
        data = b''
        for entry in entries:
            # First get the input data and put it in a file. If not available,
            # try later.
            if not entry.ObtainContents(fake_size=fake_size):
                return None, None, None
            data += entry.GetData()
        uniq = self.GetUniqueName()
        fname = tools.get_output_filename(f'{prefix}.{uniq}')
        tools.write_file(fname, data)
        return data, fname, uniq

    @classmethod
    def create_fake_dir(cls):
        """Create the directory for fake files"""
        cls.fake_dir = tools.get_output_filename('binman-fake')
        if not os.path.exists(cls.fake_dir):
            os.mkdir(cls.fake_dir)
        tout.notice(f"Fake-blob dir is '{cls.fake_dir}'")

    def ensure_props(self):
        """Raise an exception if properties are missing

        Args:
            prop_list (list of str): List of properties to check for

        Raises:
            ValueError: Any property is missing
        """
        not_present = []
        for prop in self.required_props:
            if not prop in self._node.props:
                not_present.append(prop)
        if not_present:
            self.Raise(f"'{self.etype}' entry is missing properties: {' '.join(not_present)}")
