# Elasticity
Utility for managing indexes and their data on elastic search clusters without incurring downtime.

## installation and use
Elasticity is in [PyPi](https://pypi.python.org/pypi/elasticity) and can be installed using pip:
```shell
> pip install elasticity
```
or easy_install:
```shell
> easy_install elasticity
```

Then run the elasticity command:
```shell
>  elasticity --help
usage: elasticity [options]

Elasticity

optional arguments:
  -h, --help            show this help message and exit
  -f CONFIG_FILE, --config-file CONFIG_FILE
                        Configuration file, defaults to elasticity.config
  -i HOST, --host HOST  The host to connect to
  -p PORT, --port PORT  The port to connect to on host
  -un USERNAME, --username USERNAME
                        The username
  -pw PASSWORD, --password PASSWORD
                        The password
  -do, --delete-old-index
                        Delete old index after updating
  -co, --close-old-index
                        Close old index after updating
  -c, --create          Create new stuff
  -u, --update          Update existing stuff

```

- The `-f` argument defines a configuration file to use, this value defaults to `elasticity.config`
- The `-i` specifies the host to connect to, this value defaults to `localhost`
- The `-p` specifies the port on the host to connect on, this value defaults to `9300`
- The `-un` optional argument that specifies what username to use
- The `-pw` optional argument that specifies what password to use
- The `-c` argument tells elasticity to operate in create mode, new indexes and aliases are created
- The `-u` argument tells elasticity to operate in update mode, new indexes and aliases are created and data is copied from the old index
- The `-do` argument only takes affect in update mode and deletes the old index after data has been coppied to the new index and the alias is pointed to the new index
- The `-co` argument only takes affect in update mode and closes the old index after data has been coppied to the new index and the alias is pointed to the new index

## configuration
Elasticity uses yaml for configuration.  Here's a sample that uses all of the options:

```yaml

indexes:
    - # books index
        name: books_idx
        alias: books
        settings_file: src/es/books-settings.json
        mappings:
            - 
                doc_type: hardcover_book
                mapping_file: src/es/hardcover_book-mapping.json
            - 
                doc_type: paperback_book
                mapping_file: src/es/paperback_book-mapping.json

    - # users index
        name: users_idx
        alias: users
        settings_file: src/es/users-settings.json
        mappings:
            - 
                doc_type: user
                mapping_file: src/es/user-mapping.json

```

The configuration file has a single root attribute named `indexes` that is a list of indexes and their settings\mappings.

Each index definition has the following configuration attributes:

- `name` (required) name of the index.  When the index is created it will be created with a unique id appended to the end of this name, ie: `users_20150101030512`
- `alias` (required) the name of an alias to create and map to the index created.  This is the index that applications using elastic search should use.
- `settings_file` (optional) the name of a file containing a json payload that describes the settings for the index.
- `mappings` (optional) a list of mappings to add to the index
  - `doc_type` (required) the document type for the given mapping
  - `mapping_file` (required) the name of a file containing a json payload the describes the mapping for the given doc-type in the given index.

## create mode
When in create mode (`--create` or `-c`) elasticity will do the following for each index defined in the configuration file:
- create a new index
- if there is a settings file
  - close the index
  - apply the settings to the new index
  - reopen the index
- apply any mappings to the new index
- delete the alias if it exists already
- create the alias pointed at the new index

## update mode
When in update mode (`--update` or `-u`) elasticity will do the following for each index defined in the configuration file:
- create a new index
- if there is a settings file
  - close the index
  - apply the settings to the new index
  - reopen the index
- apply any mappings to the new index
- copy all data for the defined mappings from the existing alias to the new index
- delete the existing alias
- create a new alias (with the same name) pointed at the new index
