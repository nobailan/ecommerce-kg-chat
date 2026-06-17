# graph/src/datasync/table_sync.py

from utils import MysqlReader, Neo4jWriter

class TableSynchronizer:
    def __init__(self):
        self.mysql_reader = MysqlReader()
        self.neo4j_writer = Neo4jWriter()

    def sync_base_category1(self):
        sql = """
              SELECT id,
                     name
              FROM base_category1
              """

        self.neo4j_writer.write_nodes(label="Category1", batch_data=self.mysql_reader.read_data(sql))

    def sync_base_category2(self):
        sql = """
              SELECT id,
                     name
              FROM base_category2
              """
        self.neo4j_writer.write_nodes(label="Category2", batch_data=self.mysql_reader.read_data(sql))

    def sync_base_category3(self):
        sql = """
              SELECT id,
                     name
              FROM base_category3
              """
        self.neo4j_writer.write_nodes(label="Category3", batch_data=self.mysql_reader.read_data(sql))

    def sync_category1_category2(self):
        sql = """
              SELECT c2.id           AS start_id,
                     c2.category1_id AS end_id
              FROM base_category2 c2
              """

        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="Category2",
                                              end_node_label="Category1",
                                              relationships=relationships,
                                              relationship_type='Belong')

    def sync_category2_category3(self):
        sql = """
              SELECT c3.id           AS start_id,
                     c3.category2_id AS end_id
              FROM base_category3 c3
              """

        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="Category3",
                                              end_node_label="Category2",
                                              relationships=relationships,
                                              relationship_type='Belong')

    def sync_base_attr(self):
        sql = """
              SELECT id,
                     attr_name AS name
              FROM base_attr_info
              """
        self.neo4j_writer.write_nodes(label="BaseAttr", batch_data=self.mysql_reader.read_data(sql))

    def sync_base_attr_category(self):
        sql = """
              SELECT id          end_id,
                     category_id start_id,
                     category_level
              FROM base_attr_info
              """
        relationships = self.mysql_reader.read_data(sql)
        base_attr_category3 = [
            {
                "start_id": r['start_id'],
                "end_id": r['end_id']
            } for r in relationships if r['category_level'] == 3
        ]
        self.neo4j_writer.write_relationships(start_node_label="Category3",
                                              end_node_label="BaseAttr",
                                              relationships=base_attr_category3,
                                              relationship_type='Have')

        base_attr_category2 = [
            {
                "start_id": r['start_id'],
                "end_id": r['end_id']
            } for r in relationships if r['category_level'] == 2
        ]
        self.neo4j_writer.write_relationships(start_node_label="Category2",
                                              end_node_label="BaseAttr",
                                              relationships=base_attr_category2,
                                              relationship_type='Have')

        base_attr_category1 = [
            {
                "start_id": r['start_id'],
                "end_id": r['end_id']
            } for r in relationships if r['category_level'] == 1
        ]
        self.neo4j_writer.write_relationships(start_node_label="Category1",
                                              end_node_label="BaseAttr",
                                              relationships=base_attr_category1,
                                              relationship_type='Have')

    def sync_base_attr_value(self):
        sql = """
              select id,
                     value_name name
              from base_attr_value
              """
        self.neo4j_writer.write_nodes(label="BaseAttrValue", batch_data=self.mysql_reader.read_data(sql))

    def sync_base_attr_value_attr(self):
        sql = """
              select id      end_id,
                     attr_id start_id
              from base_attr_value
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="BaseAttr",
                                              end_node_label="BaseAttrValue",
                                              relationships=relationships,
                                              relationship_type='Have')

    def sync_spu(self):
        sql = """
              select id,
                     spu_name name
              from spu_info
              """
        self.neo4j_writer.write_nodes(label="SPU", batch_data=self.mysql_reader.read_data(sql))

    def sync_spu_category3(self):
        sql = """
              select id           start_id,
                     category3_id end_id
              from spu_info
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="SPU",
                                              end_node_label="Category3",
                                              relationships=relationships,
                                              relationship_type='Belong')

    def sync_sale_attr(self):
        sql = """
              select id,
                     sale_attr_name name
              from spu_sale_attr
              """
        self.neo4j_writer.write_nodes(label="SaleAttr", batch_data=self.mysql_reader.read_data(sql))

    def sync_sale_attr_spu(self):
        sql = """
              select id     end_id,
                     spu_id start_id
              from spu_sale_attr
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="SPU",
                                              end_node_label="SaleAttr",
                                              relationships=relationships,
                                              relationship_type='Have')

    def sync_sale_attr_value(self):
        sql = """
              select id,
                     sale_attr_value_name name
              from spu_sale_attr_value
              """
        self.neo4j_writer.write_nodes(label="SaleAttrValue", batch_data=self.mysql_reader.read_data(sql))

    def sync_sale_attr_value_attr(self):
        sql = """
              select a.id start_id,
                     v.id end_id
              from spu_sale_attr_value v
                       join spu_sale_attr a on v.spu_id = a.spu_id and v.base_sale_attr_id = a.base_sale_attr_id
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="SaleAttr",
                                              end_node_label="SaleAttrValue",
                                              relationships=relationships,
                                              relationship_type='Have')

    def sync_sku(self):
        sql = """
              select id,
                     sku_name name
              from sku_info
              """
        self.neo4j_writer.write_nodes(label="SKU", batch_data=self.mysql_reader.read_data(sql))

    def sync_sku_base_attr_value(self):
        sql = """
              select sku_id   start_id,
                     value_id end_id
              from sku_attr_value
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="SKU",
                                              end_node_label="BaseAttrValue",
                                              relationships=relationships,
                                              relationship_type='Have')

    def sync_sku_sale_attr_value(self):
        sql = """
              select sku_id             start_id,
                     sale_attr_value_id end_id
              from sku_sale_attr_value
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="SKU",
                                              end_node_label="SaleAttrValue",
                                              relationships=relationships,
                                              relationship_type='Have')

    def sync_sku_spu(self):
        sql = """
              select id     start_id,
                     spu_id end_id
              from sku_info
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="SKU",
                                              end_node_label="SPU",
                                              relationships=relationships,
                                              relationship_type='Belong')

    def sync_base_trademark(self):
        sql = """
              select id,
                     tm_name name
              from base_trademark
              """
        self.neo4j_writer.write_nodes(label="BaseTrademark", batch_data=self.mysql_reader.read_data(sql))

    def sync_base_trademark_spu(self):
        sql = """
              select id    start_id,
                     tm_id end_id
              from spu_info
              """
        relationships = self.mysql_reader.read_data(sql)
        self.neo4j_writer.write_relationships(start_node_label="SPU",
                                              end_node_label="BaseTrademark",
                                              relationships=relationships,
                                              relationship_type='Belong')

if __name__ == '__main__':
    synchronizer = TableSynchronizer()
    # 同步分类系统
    synchronizer.sync_base_category1()
    synchronizer.sync_base_category2()
    synchronizer.sync_base_category3()
    synchronizer.sync_category1_category2()
    synchronizer.sync_category2_category3()

    # 同步属性系统
    synchronizer.sync_base_attr()
    synchronizer.sync_base_attr_category()
    synchronizer.sync_base_attr_value()
    synchronizer.sync_base_attr_value_attr()

    # 同步SPU
    synchronizer.sync_spu()
    synchronizer.sync_spu_category3()

    # 同步销售属性
    synchronizer.sync_sale_attr()
    synchronizer.sync_sale_attr_spu()

    # 同步销售属性值
    synchronizer.sync_sale_attr_value()
    synchronizer.sync_sale_attr_value_attr()

    # 同步SKU
    synchronizer.sync_sku()
    synchronizer.sync_sku_spu()
    synchronizer.sync_sku_base_attr_value()
    synchronizer.sync_sku_sale_attr_value()

    # 同步品牌
    synchronizer.sync_base_trademark()
    synchronizer.sync_base_trademark_spu()