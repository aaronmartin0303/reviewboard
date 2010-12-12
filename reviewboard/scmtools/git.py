                                        InvalidRevisionFormatError, \
GIT_DIFF_EMPTY_CHANGESET_SIZE = 3
GIT_DIFF_PREFIX = re.compile('^[ab]/')


class ShortSHA1Error(InvalidRevisionFormatError):
    def __init__(self, path, revision, *args, **kwargs):
        InvalidRevisionFormatError.__init__(
            self,
            path=path,
            revision=revision,
            detail='The SHA1 is too short. Make sure the diff is generated '
                   'with `git diff --full-index`.',
            *args, **kwargs)


        except (FileNotFoundError, InvalidRevisionFormatError):

        elif revision != PRE_CREATION:
            self.client.validate_sha1_format(file_str, revision)

        return ['diff_path', 'parent_diff_path']
            i, file_info = self._parse_diff(i)
            if file_info:
                self._ensure_file_has_required_fields(file_info)
                self.files.append(file_info)
    def _parse_diff(self, linenum):
        if self.lines[linenum].startswith("diff --git"):
            return self._parse_git_diff(linenum)
        else:
            return linenum + 1, None

    def _parse_git_diff(self, linenum):
        # First check if it is a new file with no content or
        # a file mode change with no content or
        # a deleted file with no content
        # then skip

        try:
            if self._is_empty_change(linenum):
                linenum += GIT_DIFF_EMPTY_CHANGESET_SIZE
                return linenum, None
        except IndexError:
            # This means this is the only bit left in the file
            linenum += GIT_DIFF_EMPTY_CHANGESET_SIZE
            return linenum, None

        # Now we have a diff we are going to use so get the filenames + commits
        file_info = File()
        file_info.data = self.lines[linenum] + "\n"
        file_info.binary = False
        diff_line = self.lines[linenum].split()

        try:
            # Need to remove the "a/" and "b/" prefix
            file_info.origFile = GIT_DIFF_PREFIX.sub("", diff_line[-2])
            file_info.newFile = GIT_DIFF_PREFIX.sub("", diff_line[-1])
        except ValueError:
            raise DiffParserError('The diff file is missing revision '
                                  'information', linenum)
        linenum += 1

        # We have no use for recording this info so skip it
        if self._is_newfile_or_deleted_change(linenum):
            linenum += 1
        elif self._is_mode_change(linenum):
            linenum += 2

        if self._is_index_range_line(linenum):
            index_range = self.lines[linenum].split(None, 2)[1]

            if '..' in index_range:
                file_info.origInfo, file_info.newInfo = index_range.split("..")

            if self.pre_creation_regexp.match(file_info.origInfo):
                file_info.origInfo = PRE_CREATION

            linenum += 1

        # Get the changes
        while linenum < len(self.lines):
            if self._is_git_diff(linenum):
                return linenum, file_info

            if self._is_binary_patch(linenum):
                file_info.binary = True
                return linenum + 1, file_info

            if self._is_diff_fromfile_line(linenum):
                if self.lines[linenum].split()[1] == "/dev/null":
                    file_info.origInfo = PRE_CREATION

            file_info.data += self.lines[linenum] + "\n"
            linenum += 1

        return linenum, file_info

    def _is_empty_change(self, linenum):
        next_diff_start = self.lines[linenum + GIT_DIFF_EMPTY_CHANGESET_SIZE]
        next_line = self.lines[linenum + 1]
        return ((next_line.startswith("new file mode") or
                 next_line.startswith("old mode") or
                 next_line.startswith("deleted file mode"))
                and next_diff_start.startswith("diff --git"))

    def _is_newfile_or_deleted_change(self, linenum):
        line = self.lines[linenum]

        return (line.startswith("new file mode")
                or line.startswith("deleted file mode"))

    def _is_mode_change(self, linenum):
        return (self.lines[linenum].startswith("old mode")
                and self.lines[linenum + 1].startswith("new mode"))

    def _is_index_range_line(self, linenum):
        return (linenum < len(self.lines) and
                self.lines[linenum].startswith("index "))

    def _is_git_diff(self, linenum):
        return self.lines[linenum].startswith('diff --git')

    def _is_binary_patch(self, linenum):
        line = self.lines[linenum]

        return (line.startswith("Binary files") or
                line.startswith("GIT binary patch"))

    def _is_diff_fromfile_line(self, linenum):
        return (linenum + 1 < len(self.lines) and
                (self.lines[linenum].startswith('--- ') and
                    self.lines[linenum + 1].startswith('+++ ')))

    def _ensure_file_has_required_fields(self, file_info):
        """
        This is needed so that there aren't explosions higher up
        the chain when the web layer is expecting a string object.

        """
        for attr in ('origInfo', 'newInfo', 'data'):
            if getattr(file_info, attr) is None:
                setattr(file_info, attr, '')
    FULL_SHA1_LENGTH = 40

                # See if we have a permissions error
                if not os.access(self.git_dir, os.R_OK):
                    raise SCMError(_("Permission denied accessing the local "
                                     "Git repository '%s'") % self.git_dir)
                else:
                    raise SCMError(_('Unable to retrieve information from '
                                     'local Git repository'))
            self.validate_sha1_format(path, revision)

            self.validate_sha1_format(path, revision)

    def validate_sha1_format(self, path, sha1):
        """Validates that a SHA1 is of the right length for this repository."""
        if self.raw_file_url and len(sha1) != self.FULL_SHA1_LENGTH:
            raise ShortSHA1Error(path, sha1)
