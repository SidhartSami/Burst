"""
package_extensions.py - Packages extension-chrome and extension-firefox into
clean ZIP archives with POSIX forward slashes (required by Mozilla AMO & Chrome Web Store).
"""
import os
import zipfile

def make_extension_zip(source_dir, output_zip):
    print(f"Packaging {source_dir} -> {output_zip}...")
    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, source_dir)
                # POSIX forward slash mandatory for ZIP spec (prevents AMO backslash error)
                posix_path = rel_path.replace(os.sep, '/')
                zf.write(abs_path, posix_path)
    print(f"Successfully created {output_zip}")

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    make_extension_zip(os.path.join(base_dir, "extension-firefox"), os.path.join(base_dir, "extension-firefox.zip"))
    make_extension_zip(os.path.join(base_dir, "extension-chrome"), os.path.join(base_dir, "extension-chrome.zip"))
