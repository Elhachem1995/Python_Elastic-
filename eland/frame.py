"""
DataFrame
---------
An efficient 2D container for potentially mixed-type time series or other
labeled data series.

The underlying data resides in Elasticsearch and the API aligns as much as
possible with pandas.DataFrame API.

This allows the eland.DataFrame to access large datasets stored in Elasticsearch,
without storing the dataset in local memory.

Implementation Details
----------------------

Elasticsearch indexes can be configured in many different ways, and these indexes
utilise different data structures to pandas.DataFrame.

eland.DataFrame operations that return individual rows (e.g. df.head()) return
_source data. If _source is not enabled, this data is not accessible.

Similarly, only Elasticsearch searchable fields can be searched or filtered, and
only Elasticsearch aggregatable fields can be aggregated or grouped.

"""
import eland as ed

from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search

import pandas as pd

from pandas.core.arrays.sparse import BlockIndex

class DataFrame():
    """
    pandas.DataFrame like API that proxies into Elasticsearch index(es).

    Parameters
    ----------
    client : eland.Client
        A reference to a Elasticsearch python client

    index_pattern : str
        An Elasticsearch index pattern. This can contain wildcards (e.g. filebeat-*).

    operations: list of operation
        A list of Elasticsearch analytics operations e.g. filter, aggregations etc.

    See Also
    --------

    Examples
    --------

    import eland as ed
    client = ed.Client(Elasticsearch())
    df = ed.DataFrame(client, 'reviews')
    df.head()
       reviewerId  vendorId  rating              date
    0           0         0       5  2006-04-07 17:08
    1           1         1       5  2006-05-04 12:16
    2           2         2       4  2006-04-21 12:26
    3           3         3       5  2006-04-18 15:48
    4           3         4       5  2006-04-18 15:49

    Notice that the types are based on Elasticsearch mappings

    Notes
    -----
    If the Elasticsearch index is deleted or index mappings are changed after this
    object is created, the object is not rebuilt and so inconsistencies can occur.

    """
    def __init__(self,
                 client,
                 index_pattern,
                 mappings=None,
                 operations=None):
        self.client = ed.Client(client)
        self.index_pattern = index_pattern

        # Get and persist mappings, this allows us to correctly
        # map returned types from Elasticsearch to pandas datatypes
        if mappings is None:
            self.mappings = ed.Mappings(self.client, self.index_pattern)
        else:
            self.mappings = mappings

        # Initialise a list of 'operations'
        # these are filters
        self.operations = []
        if operations is not None:
            self.operations.extend(operations)

    def _es_results_to_pandas(self, results):
        """
        Parameters
        ----------
        results: dict
            Elasticsearch results from self.client.search

        Returns
        -------
        df: pandas.DataFrame
            _source values extracted from results and mapped to pandas DataFrame
            dtypes are mapped via Mapping object

        Notes
        -----
        Fields containing lists in Elasticsearch don't map easily to pandas.DataFrame
        For example, an index with mapping:
        ```
        "mappings" : {
          "properties" : {
            "group" : {
              "type" : "keyword"
            },
            "user" : {
              "type" : "nested",
              "properties" : {
                "first" : {
                  "type" : "keyword"
                },
                "last" : {
                  "type" : "keyword"
                }
              }
            }
          }
        }
        ```
        Adding a document:
        ```
        "_source" : {
          "group" : "amsterdam",
          "user" : [
            {
              "first" : "John",
              "last" : "Smith"
            },
            {
              "first" : "Alice",
              "last" : "White"
            }
          ]
        }
        ```
        (https://www.elastic.co/guide/en/elasticsearch/reference/current/nested.html)
        this would be transformed internally (in Elasticsearch) into a document that looks more like this:
        ```
        {
          "group" :        "amsterdam",
          "user.first" : [ "alice", "john" ],
          "user.last" :  [ "smith", "white" ]
        }
        ```
        When mapping this a pandas data frame we mimic this transformation.

        Similarly, if a list is added to Elasticsearch:
        ```
        PUT my_index/_doc/1
        {
          "list" : [
            0, 1, 2
          ]
        }
        ```
        The mapping is:
        ```
        "mappings" : {
          "properties" : {
            "user" : {
              "type" : "long"
            }
          }
        }
        ```
        TODO - explain how lists are handled (https://www.elastic.co/guide/en/elasticsearch/reference/current/array.html)
        TODO - an option here is to use Elasticsearch's multi-field matching instead of pandas treatment of lists (which isn't great)
        NOTE - using this lists is generally not a good way to use this API
        """
        def flatten_dict(y):
            out = {}

            def flatten(x, name=''):
                # We flatten into source fields e.g. if type=geo_point
                # location: {lat=52.38, lon=4.90}
                if name == '':
                    is_source_field = False
                    pd_dtype = 'object'
                else:
                    is_source_field, pd_dtype = self.mappings.source_field_pd_dtype(name[:-1])

                if not is_source_field and type(x) is dict:
                    for a in x:
                        flatten(x[a], name + a + '.')
                elif not is_source_field and type(x) is list:
                    for a in x:
                        flatten(a, name)
                elif is_source_field == True: # only print source fields from mappings (TODO - not so efficient for large number of fields and filtered mapping)
                    field_name = name[:-1]

                    # Coerce types - for now just datetime
                    if pd_dtype == 'datetime64[ns]':
                        x = pd.to_datetime(x)

                    # Elasticsearch can have multiple values for a field. These are represented as lists, so
                    # create lists for this pivot (see notes above)
                    if field_name in out:
                        if type(out[field_name]) is not list:
                            l = [out[field_name]]
                            out[field_name] = l
                        out[field_name].append(x)
                    else:
                        out[field_name] = x

            flatten(y)

            return out

        rows = []
        for hit in results['hits']['hits']:
            row = hit['_source']

            # flatten row to map correctly to 2D DataFrame
            rows.append(flatten_dict(row))

        # Create pandas DataFrame
        df = pd.DataFrame(data=rows)

        # _source may not contain all columns in the mapping
        # therefore, fill in missing columns
        # (note this returns self.columns NOT IN df.columns)
        missing_columns = list(set(self.columns) - set(df.columns))

        for missing in missing_columns:
            is_source_field, pd_dtype = self.mappings.source_field_pd_dtype(missing)
            df[missing] = None
            df[missing].astype(pd_dtype)

        # Sort columns in mapping order
        df = df[self.columns]

        return df

    def head(self, n=5):
        results = self.client.search(index=self.index_pattern, size=n)

        return self._es_results_to_pandas(results)
    
    def describe(self):
        numeric_source_fields = self.mappings.numeric_source_fields()

        # for each field we compute:
        # count, mean, std, min, 25%, 50%, 75%, max
        search = Search(using=self.client, index=self.index_pattern).extra(size=0)

        for field in numeric_source_fields:
            search.aggs.metric('extended_stats_'+field, 'extended_stats', field=field)
            search.aggs.metric('percentiles_'+field, 'percentiles', field=field)

        response = search.execute()

        results = {}

        for field in numeric_source_fields:
            values = []
            values.append(response.aggregations['extended_stats_'+field]['count'])
            values.append(response.aggregations['extended_stats_'+field]['avg'])
            values.append(response.aggregations['extended_stats_'+field]['std_deviation'])
            values.append(response.aggregations['extended_stats_'+field]['min'])
            values.append(response.aggregations['percentiles_'+field]['values']['25.0'])
            values.append(response.aggregations['percentiles_'+field]['values']['50.0'])
            values.append(response.aggregations['percentiles_'+field]['values']['75.0'])
            values.append(response.aggregations['extended_stats_'+field]['max'])
            
            # if not None
            if (values.count(None) < len(values)):
                results[field] = values

        df = pd.DataFrame(data=results, index=['count', 'mean', 'std', 'min', '25%', '50%', '75%', 'max'])
            
        return df

    @property
    def shape(self):
        """
        Return a tuple representing the dimensionality of the DataFrame.

        Returns
        -------
        shape: tuple
            0 - number of rows
            1 - number of columns
        """
        num_rows = len(self)
        num_columns = len(self.columns)

        return num_rows, num_columns

    @property
    def columns(self):
        return self.mappings.source_fields()

    def __getitem__(self, item):
        # df['a'] -> item == str
        # df['a', 'b'] -> item == (str, str) tuple
        columns = []
        if isinstance(item, str):
            if not self.mappings.is_source_field(item):
                raise TypeError('Column does not exist: [{0}]'.format(item))
            columns.append(item)
        elif isinstance(item, tuple):
            columns.extend(list(item))

        if len(columns) > 0:
            # Return new eland.DataFrame with modified mappings
            mappings = ed.Mappings(mappings=self.mappings, columns=columns)

            return DataFrame(self.client, self.index_pattern, mappings=mappings)
        """
        elif isinstance(item, BooleanFilter):
            self._filter = item.build()
            return self
        else:
            raise TypeError('Unsupported expr: [{0}]'.format(item))
        """

    def __len__(self):
        """
        Returns length of info axis, but here we use the index.
        """
        return self.client.count(index=self.index_pattern)

    # ----------------------------------------------------------------------
    # Rendering Methods

    def __repr__(self):
        return self.to_string()


    def to_string(self):
        # The return for this is display.options.max_rows
        max_rows = 60
        head_rows = max_rows / 2
        tail_rows = max_rows - head_rows

        head = self.head(max_rows)

        num_rows = len(self)

        if (num_rows > max_rows):
            # If we have a lot of rows, create a SparseDataFrame and use
            # pandas to_string logic
            # NOTE: this sparse DataFrame can't be used as the middle
            # section is all NaNs. However, it gives us potentially a nice way
            # to use the pandas IO methods.
            # TODO - if data is indexed by time series, return top/bottom of
            #   time series, rather than first max_rows items
            sdf = pd.DataFrame({item: pd.SparseArray(data=head[item],
                                                     sparse_index=
                                                     BlockIndex(
                                                         num_rows, [0, num_rows-tail_rows], [head_rows, tail_rows]))
                                for item in self.columns})

            # TODO - don't hard code max_rows - use pandas default/ES default
            return sdf.to_string(max_rows=max_rows)

        return head.to_string(max_rows=max_rows)
