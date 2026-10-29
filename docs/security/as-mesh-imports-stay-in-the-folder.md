---
order: 500
---

- **AS.** A glTF, OBJ, or COLLADA import reads the file through the shared file reader. An external buffer, image, or material library is opened only when it is embedded or stays inside that file's folder. A path that leaves the folder is not opened. The project path is not given to a geometry reader.
