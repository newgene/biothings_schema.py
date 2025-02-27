import json
import os.path

import requests
import yaml
import networkx as nx

from .utils import dict2list, merge_schema
from .settings import (
    BASE_SCHEMA,
    SCHEMAORG_JSONLD_BASE_URL,
    SCHEMAORG_VERSION_URL,
    DATATYPES,
    IGNORED_CLASS_PROPERTY
)


def load_json_or_yaml(file_path):
    """Load either json or yaml document from file path or url or JSON doc

    :arg str file_path: The path of the url doc, could be url or file path
    """
    # handle json doc
    if isinstance(file_path, dict):
        return file_path
    # handle url
    elif file_path.startswith("http"):
        with requests.get(file_path) as url:
            # check if http requests returns a success status code
            if url.status_code != 200:
                raise ValueError("Invalid URL!")
            else:
                _data = url.content
    # handle file path
    else:
        try:
            with open(file_path) as f:
                _data = f.read()
        except FileNotFoundError:
            raise ValueError("Invalid File Path!")
    try:
        if type(_data) == bytes:
            _data = _data.decode('utf-8')
        data = json.loads(_data)
    # except ValueError:               # for py<3.5
    except json.JSONDecodeError:   # for py>=3.5
        try:
            data = yaml.load(_data, Loader=yaml.SafeLoader)
        except (yaml.scanner.ScannerError,
                yaml.parser.ParserError):
            raise ValueError("Not a valid JSON or YAML format.")
    return data


def get_latest_schemaorg_version():
    """Get the latest version of schemaorg from its github"""
    versions = requests.get(SCHEMAORG_VERSION_URL).json()["releaseLog"]
    # skip pre-release entry like {"14.0": "2021-XX-XX"} and sort by the numeric version numbers
    latest = sorted([version for version, date in versions.items() if date.find('X') == -1], key=float)[-1]
    return latest


def construct_schemaorg_url(version):
    """Construct url to schemaorg jsonld file"""
    if float(version) <= 8.0:
        url = f"{SCHEMAORG_JSONLD_BASE_URL}/{version}/all-layers.jsonld"
    else:
        # >=9.0
        url = f"{SCHEMAORG_JSONLD_BASE_URL}/{version}/schemaorg-all-http.jsonld"
    return url


def load_schemaorg(version=None, verbose=False):
    """Load SchemaOrg vocabulary

    :arg float version: The schemaorg schema version, e.g 13.0
    """
    # if version is not specified, use the latest one by default
    if not version:
        try:
            version = get_latest_schemaorg_version()
        except ValueError:
            version = "13.0"
    url = construct_schemaorg_url(version)
    if verbose:
        print("Loading Schema.org schema from {}".format(url))
    try:
        return load_json_or_yaml(url)
    except ValueError:
        raise ValueError("version {} is not valid! Current latest version is {}".format(version, get_latest_schemaorg_version()))


def load_bioschemas(verbose=False):
    """Load Bioschemas vocabulary, currently cached in data folder
    """
    _ROOT = os.path.abspath(os.path.dirname(__file__))
    _path = os.path.join(_ROOT, "data", "bioschemas.json")
    if verbose:
        print(f"Loading Bioschemas schema from {_path}")
    return load_json_or_yaml(_path)


def load_base_schema(base_schema=None, verbose=False):
    """Load base schema, schema contains base classes for
       sub-classing in user schemas.
    """
    _base = base_schema or BASE_SCHEMA or []
    _base_schema = []
    if "schema.org" in _base:
        _base_schema.append(
            load_schemaorg(verbose=verbose)
        )
    if "bioschemas" in _base:
        _base_schema.append(
            load_bioschemas(verbose=verbose)
        )
    _base_schema = merge_schema(*_base_schema)
    return _base_schema


def find_parent_child_relation(record, _type="Class"):
    """Find parent child relationship from individual schema definition

    :arg dict record: a class/property definition represented by JSON in schema
    :arg str _type: either Class or Property
    """
    edges = []
    if _type not in ["Class", "Property"]:
        raise ValueError("Value of _type could only be Class or Property")
    _key = "rdfs:sub{}Of".format(_type)
    if _key in record:
        parents = record[_key]
        if isinstance(parents, list):
            for _parent in parents:
                if _parent["@id"] not in IGNORED_CLASS_PROPERTY:
                    edges.append((_parent["@id"], record["@id"]))
        elif isinstance(parents, dict):
            if parents["@id"] not in IGNORED_CLASS_PROPERTY:
                edges.append((parents["@id"], record["@id"]))
        else:
            raise ValueError('"dictionary" input is not a list or dict')
    elif "@type" in record and "http://schema.org/DataType" in record["@type"]:
        edges.append(("http://schema.org/DataType", record["@id"]))
    elif "@type" in record and record["@type"] in DATATYPES:
        edges.append((record["@type"], record["@id"]))
    else:
        pass
    return edges


def find_domain_range(record):
    """Find domain and range info of a record in schema"""
    response = {"domain": [], "range": []}
    if "http://schema.org/domainIncludes" in record:
        if isinstance(record["http://schema.org/domainIncludes"], dict):
            response["domain"] = [record["http://schema.org/domainIncludes"]["@id"]]
        elif isinstance(record["http://schema.org/domainIncludes"], list):
            response["domain"] = [_item["@id"] for _item in record["http://schema.org/domainIncludes"]]
    if "http://schema.org/rangeIncludes" in record:
        if isinstance(record["http://schema.org/rangeIncludes"], dict):
            response["range"] = [record["http://schema.org/rangeIncludes"]["@id"]]
        elif isinstance(record["http://schema.org/rangeIncludes"], list):
            response["range"] = [_item["@id"] for _item in record["http://schema.org/rangeIncludes"]]
    return (response["domain"], response["range"])


def load_schema_into_networkx(schema, load_class=True, load_property=True, load_datatype=True):
    """Construct networkx DiGraph based on Schema provided"""
    # initialize DiGraph for classes, properties and data types
    G = nx.DiGraph()
    edges = []
    classes = {}
    for record in schema["@graph"]:
        if record["@id"] in DATATYPES and load_datatype:
            G.add_node(record["@id"],
                       description=record["rdfs:comment"],
                       type="DataType")
            edges += find_parent_child_relation(record)
        elif record["@type"] == "rdfs:Class" and load_class:
            if record["@id"] in classes:
                classes[record["@id"]]["description"] = record["rdfs:comment"]
                classes[record["@id"]]["type"] = "Class"
            else:
                classes[record["@id"]] = {"description": record["rdfs:comment"],
                                          "type": "Class",
                                          "properties": [],
                                          "used_by": []}
            # add class edges
            edges += find_parent_child_relation(record)
        elif record["@type"] == "rdf:Property" and load_property:
            _domain, _range = find_domain_range(record)
            _inverse = record.get("http://schema.org/inverseOf")
            if _inverse:
                _inverse = _inverse["@id"]
            G.add_node(record["@id"],
                       description=record["rdfs:comment"],
                       domain=_domain,
                       range=_range,
                       inverse=_inverse,
                       type="Property")
            property_info = {'description': record["rdfs:comment"],
                             'domain': _domain,
                             'range': _range,
                             'inverse': _inverse,
                             'uri': record["@id"]}
            for _id in _domain:
                if _id not in DATATYPES:
                    if _id not in classes:
                        classes[_id] = {"properties": [property_info],
                                        "type": "Class",
                                        "used_by": []}
                    else:
                        classes[_id]["properties"].append(property_info)
            for _id in _range:
                if _id not in DATATYPES:
                    if _id not in classes:
                        classes[_id] = {"used_by": [property_info],
                                        "type": "Class",
                                        "properties": []}
                    else:
                        classes[_id]["used_by"].append(property_info)
            edges += find_parent_child_relation(record, _type="Property")
    G.add_edges_from(edges)
    G.add_nodes_from(list(classes.items()))

    return G


def load_schema_class_into_networkx(schema, preload_schemaorg=False):
    """Constuct networkx DiGraph based on Schema provided"""
    # preload all schema from schemaorg latest version
    if preload_schemaorg:
        G = load_schema_class_into_networkx(preload_schemaorg,
                                            preload_schemaorg=False)
    else:
        G = nx.DiGraph()
    for record in schema["@graph"]:
        if record["@type"] == "rdfs:Class" and record["@id"] not in DATATYPES:
            G.add_node(record["@id"],
                       description=record["rdfs:comment"])
            if "rdfs:subClassOf" in record:
                parents = record["rdfs:subClassOf"]
                if isinstance(parents, list):
                    for _parent in parents:
                        G.add_edge(_parent["@id"],
                                   record["@id"])
                elif isinstance(parents, dict):
                    G.add_edge(parents["@id"],
                               record["@id"])
                else:
                    raise ValueError('"dictionary" input is not a list or dict')
            else:
                pass
    return G


def load_schema_property_into_networkx(schema, preload_schemaorg=False):
    """Constuct networkx DiGraph based on Schema provided"""
    # preload all schema from schemaorg latest version
    if preload_schemaorg:
        G = load_schema_property_into_networkx(preload_schemaorg,
                                               preload_schemaorg=False)
    else:
        G = nx.DiGraph()
    for record in schema["@graph"]:
        if record["@type"] == "rdf:Property":
            G.add_node(record["@id"],
                       uri=record["@id"],
                       description=record["rdfs:comment"])
            if "rdfs:subPropertyOf" in record:
                parents = record["rdfs:subPropertyOf"]
                if isinstance(parents, list):
                    for _parent in parents:
                        G.add_edge(_parent["@id"],
                                   record["@id"])
                elif isinstance(parents, dict):
                    G.add_edge(parents["@id"],
                               record["@id"])
                else:
                    raise ValueError('"dictionary" input is not a list or dict')
            else:
                pass
    return G


def load_schema_datatype_into_networkx(schema):
    """Construct networkx DiGraph for data types based on Schema provided"""

    G = nx.DiGraph()
    for record in schema["@graph"]:
        if record["@id"] in DATATYPES:
            G.add_node(record["@id"],
                       uri=record["@id"],
                       description=record["rdfs:comment"])
            if "rdfs:subClassOf" in record:
                parents = dict2list(record["rdfs:subClassOf"])
                for _parent in parents:
                    if _parent["@id"] != "rdfs:Class":
                        G.add_edge(_parent["@id"],
                                   record["@id"])
            elif "@type" in record and "http://schema.org/DataType" in record["@type"]:
                G.add_edge("http://schema.org/DataType", record["@id"])
    return G


def get_clean_schema_context(schema):
    """return the clean prefix list from "@content" for only those are used"""
    _schema = load_json_or_yaml(schema)
    context = _schema.get('@context', [])
    if context:
        graph = _schema.get('@graph', [])
        graph = json.dumps(graph)
        used_prefix_li = []
        for prefix in context:
            if graph.find(prefix + ":") != -1:
                used_prefix_li.append(prefix)
        clean_context = {
            "@context": {prefix: context[prefix] for prefix in sorted(set(used_prefix_li))}
        }
        return clean_context
    else:
        print('No "@context" found in the schema')
