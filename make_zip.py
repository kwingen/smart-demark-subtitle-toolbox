"""打包智能去码字幕工具箱 v1.2"""
import zipfile, os

zip_path = r'C:\Users\Administrator\Desktop\work\智能去码字幕工具箱_v1.2.zip'
src_dir = os.getcwd()

files_to_pack = []
for f in sorted(os.listdir(src_dir)):
    if f.endswith('.pyc') or f == '__pycache__' or f == '.gitignore' or f.startswith('_') or f.startswith('test_'):
        continue
    if os.path.isfile(os.path.join(src_dir, f)):
        files_to_pack.append(f)

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for fname in files_to_pack:
        fpath = os.path.join(src_dir, fname)
        # UTF-8 filename for zip
        info = zipfile.ZipInfo(fname.replace('_', '_'), date_time=(2025,5,16,0,0,0))
        info.flag_bits |= 0x800  # UTF-8 flag
        with open(fpath, 'rb') as f:
            zf.writestr(info, f.read())
        print(f'  Added: {fname}')

print(f'\nZip created: {zip_path}')
size = os.path.getsize(zip_path)
print(f'Size: {size} bytes ({size/1024:.1f} KB)')
