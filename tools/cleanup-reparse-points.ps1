# Shared, fail-closed reparse-point checks for cleanup scripts. This file only
# defines functions; dot-sourcing it does not inspect files or change anything.

function Get-CleanupReparseTag {
    param([Parameter(Mandatory=$true)][string]$LiteralPath)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Reparse-tag verification requires Windows.'
    }
    if (-not ('AssessmentCleanup.NativeReparseTag' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace AssessmentCleanup {
    public static class NativeReparseTag {
        [StructLayout(LayoutKind.Sequential)]
        private struct FileAttributeTagInfo {
            public uint FileAttributes;
            public uint ReparseTag;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(
            string path, uint access, uint share, IntPtr security,
            uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetFileInformationByHandleEx(
            SafeFileHandle file, int informationClass,
            out FileAttributeTagInfo information, uint size);

        public static uint Read(string path) {
            // FILE_READ_ATTRIBUTES; share read/write/delete; OPEN_EXISTING.
            // BACKUP_SEMANTICS opens directories. OPEN_REPARSE_POINT inspects
            // the reparse point itself instead of following its target.
            using (SafeFileHandle handle = CreateFileW(
                path, 0x80, 0x7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero)) {
                if (handle.IsInvalid) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                FileAttributeTagInfo information;
                if (!GetFileInformationByHandleEx(handle, 9, out information, 8)) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                if ((information.FileAttributes & 0x400) == 0) {
                    throw new InvalidOperationException("Reparse attributes changed during verification.");
                }
                return information.ReparseTag;
            }
        }
    }
}
'@ -ErrorAction Stop
    }
    return [AssessmentCleanup.NativeReparseTag]::Read($LiteralPath)
}

function Assert-SafeCleanupReparsePoint {
    param([Parameter(Mandatory=$true)][object]$Item)
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) { return }
    try {
        [uint32]$tag = Get-CleanupReparseTag -LiteralPath $Item.FullName
    } catch {
        throw "Cannot safely verify reparse point '$($Item.FullName)'. Cleanup stopped: $($_.Exception.Message)"
    }
    # Only the exact Windows Cloud Files family is permitted. These placeholders
    # (including OneDrive) are not name surrogates. Do not accept all Microsoft
    # tags, arbitrary non-surrogate tags, or the unused IO_REPARSE_TAG_ONEDRIVE.
    # Verified 2026-09-16: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/c8e77b37-3909-4fe6-a4ea-2b9d423b1ee4
    $cloudTags = @(
        '9000001A', '9000101A', '9000201A', '9000301A',
        '9000401A', '9000501A', '9000601A', '9000701A',
        '9000801A', '9000901A', '9000A01A', '9000B01A',
        '9000C01A', '9000D01A', '9000E01A', '9000F01A'
    )
    $tagHex = '{0:X8}' -f $tag
    if (($tag -band 0x20000000) -ne 0 -or $cloudTags -notcontains $tagHex) {
        throw "Refusing a symbolic link, junction, or unsupported reparse point (0x$tagHex): $($Item.FullName). Review this location manually."
    }
}
