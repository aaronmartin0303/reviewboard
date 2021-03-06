# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging
import os
import re
import weakref

from django.utils import six
from django.utils.translation import ugettext as _

from reviewboard.diffviewer.parser import DiffParser
from reviewboard.scmtools.certs import Certificate
from reviewboard.scmtools.core import SCMTool, HEAD, PRE_CREATION, UNKNOWN
from reviewboard.scmtools.errors import (AuthenticationError,
                                         RepositoryNotFoundError,
                                         SCMError,
                                         UnverifiedCertificateError)
from reviewboard.scmtools.svn.subvertpy import Client, imported_dependency
if not imported_dependency:  # not installed/couldn't be imported
    from reviewboard.scmtools.svn.pysvn import (
        Client, imported_dependency)
from reviewboard.ssh import utils as sshutils


# Register these URI schemes so we can handle them properly.
sshutils.ssh_uri_schemes.append('svn+ssh')

sshutils.register_rbssh('SVN_SSH')


class SVNCertificateFailures:
    """SVN HTTPS certificate failure codes.

    These map to the various SVN HTTPS certificate failures in libsvn.
    """
    NOT_YET_VALID = 1 << 0
    EXPIRED       = 1 << 1
    CN_MISMATCH   = 1 << 2
    UNKNOWN_CA    = 1 << 3


class SVNTool(SCMTool):
    name = "Subversion"
    uses_atomic_revisions = True
    supports_authentication = True
    supports_post_commit = True
    dependencies = {
        'modules': [Client.required_module],
    }

    def __init__(self, repository):
        self.repopath = repository.path
        if self.repopath[-1] == '/':
            self.repopath = self.repopath[:-1]

        super(SVNTool, self).__init__(repository)

        if repository.local_site:
            local_site_name = repository.local_site.name
        else:
            local_site_name = None

        self.config_dir, self.client = \
            self.build_client(self.repopath,
                              repository.username, repository.password,
                              local_site_name)

        # If we assign a function to the pysvn Client that accesses anything
        # bound to SVNClient, it'll end up keeping a reference and a copy of
        # the function for every instance that gets created, and will never
        # let go. This will cause a rather large memory leak.
        #
        # The solution is to access a weakref instead. The weakref will
        # reference the repository, but it will safely go away when needed.
        # The function we pass can access that without causing the leaks
        repository_ref = weakref.ref(repository)
        self.client.callback_ssl_server_trust_prompt = \
            lambda trust_dict: \
            SVNTool._ssl_server_trust_prompt(trust_dict, repository_ref())

        # 'svn diff' produces patches which have the revision string localized
        # to their system locale. This is a little ridiculous, but we have to
        # deal with it because not everyone uses post-review.
        self.revision_re = re.compile("""
            ^(\(([^\)]+)\)\s)?              # creating diffs between two branches
                                            # of a remote repository will insert
                                            # extra "relocation information" into
                                            # the diff.

            (?:\d+-\d+-\d+\ +               # svnlook-style diffs contain a
               \d+:\d+:\d+\ +               # timestamp on each line before the
               [A-Z]+\ +)?                  # revision number.  This here is
                                            # probably a really crappy way to
                                            # express that, but oh well.

            \ *\((?:
                [Rr]ev(?:ision)?|           # english - svnlook uses 'rev 0'
                                            #           while svn diff uses
                                            #           'revision 0'
                revisión:|                  # espanol
                révision|                   # french
                revisione|                  # italian
                リビジョン|                 # japanese
                리비전|                     # korean
                revisjon|                   # norwegian
                wersja|                     # polish
                revisão|                    # brazilian portuguese
                版本                        # simplified chinese
            )\ (\d+)\)$
            """, re.VERBOSE)

    def get_file(self, path, revision=HEAD):
        return self.client.get_file(path, revision)

    def get_keywords(self, path, revision=HEAD):
        return self.client.get_keywords(path, revision)

    def get_branches(self):
        """Returns a list of branches.

        This assumes the standard layout in the repository."""
        return self.client.branches

    def get_commits(self, start):
        """Return a list of commits."""
        return self.client.get_commits(start)

    def get_change(self, revision):
        """Get an individual change.

        This returns a Commit object containing the details of the commit.
        """
        cache_key = self.repository.get_commit_cache_key(revision)

        return self.client.get_change(revision, cache_key)

    def normalize_patch(self, patch, filename, revision=HEAD):
        """
        If using Subversion, we need not only contract keywords in file, but
        also in the patch. Otherwise, if a file with expanded keyword somehow
        ends up in the repository (e.g. by first checking in a file without
        svn:keywords and then setting svn:keywords in the repository), RB
        won't be able to apply a patch to such file.
        """
        if revision != PRE_CREATION:
            keywords = self.get_keywords(filename, revision)

            if keywords:
                return self.client.collapse_keywords(patch, keywords)

        return patch

    def parse_diff_revision(self, file_str, revision_str, *args, **kwargs):
        # Some diffs have additional tabs between the parts of the file
        # revisions
        revision_str = revision_str.strip()

        if revision_str == "(working copy)":
            return file_str, HEAD

        # "(revision )" is generated by a few weird tools (like IntelliJ). If
        # in the +++ line of the diff, it means HEAD, and in the --- line, it
        # means PRE_CREATION. Since the more important use case is parsing the
        # source revision, we treat it as a new file. See bugs 1937 and 2632.
        if revision_str == "(revision )":
            return file_str, PRE_CREATION

        # Binary diffs don't provide revision information, so we set a fake
        # "(unknown)" in the SVNDiffParser. This will never actually appear
        # in SVN diffs.
        if revision_str == "(unknown)":
            return file_str, UNKNOWN

        m = self.revision_re.match(revision_str)
        if not m:
            raise SCMError("Unable to parse diff revision header '%s'" %
                           revision_str)

        relocated_file = m.group(2)
        revision = m.group(3)

        if revision == "0":
            revision = PRE_CREATION

        if relocated_file:
            if not relocated_file.startswith("..."):
                raise SCMError("Unable to parse SVN relocated path '%s'" %
                               relocated_file)

            file_str = "%s/%s" % (relocated_file[4:], file_str)

        return file_str, revision

    def get_filenames_in_revision(self, revision):
        return self.client.get_filenames_in_revision(revision)

    def get_repository_info(self):
        return self.client.repository_info

    def get_fields(self):
        return ['basedir', 'diff_path']

    def get_parser(self, data):
        return SVNDiffParser(data)

    @classmethod
    def _ssl_server_trust_prompt(cls, trust_dict, repository):
        """Callback for SSL cert verification.

        This will be called when accessing a repository with an SSL cert.
        We will look up a matching cert in the database and see if it's
        accepted.
        """
        saved_cert = repository.extra_data.get('cert', {})
        cert = trust_dict.copy()
        del cert['failures']

        return saved_cert == cert, trust_dict['failures'], False

    @staticmethod
    def on_ssl_failure(e, path, cert_data):
        logging.error('SVN: Failed to get repository information '
                      'for %s: %s' % (path, e))

        if 'callback_get_login required' in six.text_type(e):
            raise AuthenticationError(msg="Authentication failed")

        if cert_data:
            failures = cert_data['failures']

            reasons = []

            if failures & SVNCertificateFailures.NOT_YET_VALID:
                reasons.append(_('The certificate is not yet valid.'))

            if failures & SVNCertificateFailures.EXPIRED:
                reasons.append(_('The certificate has expired.'))

            if failures & SVNCertificateFailures.CN_MISMATCH:
                reasons.append(_('The certificate hostname does not '
                                 'match.'))

            if failures & SVNCertificateFailures.UNKNOWN_CA:
                reasons.append(_('The certificate is not issued by a '
                                 'trusted authority. Use the fingerprint '
                                 'to validate the certificate manually.'))

            raise UnverifiedCertificateError(
                Certificate(valid_from=cert_data['valid_from'],
                            valid_until=cert_data['valid_until'],
                            hostname=cert_data['hostname'],
                            realm=cert_data['realm'],
                            fingerprint=cert_data['finger_print'],
                            issuer=cert_data['issuer_dname'],
                            failures=reasons))

        raise RepositoryNotFoundError()

    @classmethod
    def check_repository(cls, path, username=None, password=None,
                         local_site_name=None):
        """
        Performs checks on a repository to test its validity.

        This should check if a repository exists and can be connected to.
        This will also check if the repository requires an HTTPS certificate.

        The result is returned as an exception. The exception may contain
        extra information, such as a human-readable description of the problem.
        If the repository is valid and can be connected to, no exception
        will be thrown.
        """
        super(SVNTool, cls).check_repository(path, username, password,
                                             local_site_name)

        if path.startswith('https://'):
            client = cls.build_client(path, local_site_name=local_site_name)[1]
            client.accept_ssl_certificate(path, cls.on_ssl_failure)

    @classmethod
    def accept_certificate(cls, path, local_site_name=None, certificate=None):
        """Accepts the certificate for the given repository path."""
        client = cls.build_client(path, local_site_name=local_site_name)[1]

        return client.accept_ssl_certificate(path)

    @classmethod
    def build_client(cls, repopath, username=None, password=None,
                     local_site_name=None):
        if not imported_dependency:
            raise ImportError(_(
                'SVN integration requires either subvertpy or pysvn'))
        config_dir = os.path.join(os.path.expanduser('~'), '.subversion')

        if local_site_name:
            # LocalSites can have their own Subversion config, used for
            # per-LocalSite SSH keys.
            config_dir = cls._prepare_local_site_config_dir(local_site_name)
        elif not os.path.exists(config_dir):
            cls._create_subversion_dir(config_dir)

        client = Client(config_dir, repopath)

        return config_dir, client

    @classmethod
    def _create_subversion_dir(cls, config_dir):
        try:
            os.mkdir(config_dir, 0o700)
        except OSError:
            raise IOError(
                _("Unable to create directory %(dirname)s, which is needed "
                  "for the Subversion configuration. Create this directory "
                  "and set the web server's user as the the owner.")
                % {'dirname': config_dir})

    @classmethod
    def _prepare_local_site_config_dir(cls, local_site_name):
        config_dir = os.path.join(os.path.expanduser('~'), '.subversion')

        if not os.path.exists(config_dir):
            cls._create_subversion_dir(config_dir)

        config_dir = os.path.join(config_dir, local_site_name)

        if not os.path.exists(config_dir):
            cls._create_subversion_dir(config_dir)

            with open(os.path.join(config_dir, 'config'), 'w') as fp:
                fp.write('[tunnels]\n')
                fp.write('ssh = rbssh --rb-local-site=%s\n' % local_site_name)

        return config_dir


class SVNDiffParser(DiffParser):
    BINARY_STRING = b"Cannot display: file marked as a binary type."
    PROPERTY_PATH_RE = re.compile(r'Property changes on: (.*)')

    def parse_diff_header(self, linenum, info):
        # We're looking for a SVN property change for SVN < 1.7.
        #
        # There's going to be at least 5 lines left:
        # 1) --- (blah)
        # 2) +++ (blah)
        # 3) Property changes on: <path>
        # 4) -----------------------------------------------------
        # 5) Modified: <propname>
        if (linenum + 4 < len(self.lines) and
            self.lines[linenum].startswith(b'--- (') and
            self.lines[linenum + 1].startswith(b'+++ (') and
            self.lines[linenum + 2].startswith(b'Property changes on:')):
            # Subversion diffs with property changes have no really
            # parsable format. The content of a property can easily mimic
            # the property change headers. So we can't rely upon it, and
            # can't easily display it. Instead, skip it, so it at least
            # won't break diffs.
            info['skip'] = True
            linenum += 4

            return linenum
        else:
            return super(SVNDiffParser, self).parse_diff_header(linenum, info)

    def parse_special_header(self, linenum, info):
        if (linenum + 1 < len(self.lines) and
            self.lines[linenum] == b'Index:'):
            # This is an empty Index: line. This might mean we're parsing
            # a property change.
            return linenum + 2

        linenum = super(SVNDiffParser, self).parse_special_header(linenum, info)

        if 'index' in info and linenum != len(self.lines):
            if self.lines[linenum] == self.BINARY_STRING:
                # Skip this and the svn:mime-type line.
                linenum += 2
                info['binary'] = True
                info['origFile'] = info['index']
                info['newFile'] = info['index']

                # We can't get the revision info from this diff header.
                info['origInfo'] = '(unknown)'
                info['newInfo'] = '(working copy)'

        return linenum

    def parse_after_headers(self, linenum, info):
        # We're looking for a SVN property change for SVN 1.7+.
        #
        # This differs from SVN property changes in older versions of SVN
        # in a couple ways:
        #
        # 1) The ---, +++, and Index: lines have actual filenames.
        #    Because of this, we won't hit the case in parse_diff_header
        #    above.
        # 2) There's an actual section per-property, so we could parse these
        #    out in a usable form. We'd still need a way to display that
        #    sanely, though.
        if (self.lines[linenum] == b'' and
            linenum + 2 < len(self.lines) and
            self.lines[linenum + 1].startswith('Property changes on:')):
            # Skip over the next 3 lines (blank, "Property changes on:", and
            # the "__________" divider.
            info['skip'] = True
            linenum += 3

        return linenum
